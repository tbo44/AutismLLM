"""
Web Crawler for UK Autism Sources
Extracts clean content from trusted sources with metadata
"""

import httpx
import trafilatura
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import asyncio
from urllib.parse import urljoin, urlparse
import time
import logging
from datetime import datetime
import pytz

from .sources import UK_SOURCES, Source, SourceAuthority

logger = logging.getLogger(__name__)

@dataclass
class CrawledDocument:
    url: str
    title: str
    content: str
    source_name: str
    authority: SourceAuthority
    crawled_at: datetime
    word_count: int
    section_headers: List[str]
    metadata: Dict[str, Any]

class UKAutismCrawler:
    def __init__(self, max_concurrent: int = 5, delay: float = 1.0):
        self.max_concurrent = max_concurrent
        self.delay = delay
        self.session = None
        self.uk_timezone = pytz.timezone('Europe/London')
        
    async def __aenter__(self):
        self.session = httpx.AsyncClient(
            timeout=30.0,
            limits=httpx.Limits(max_connections=self.max_concurrent),
            headers={
                'User-Agent': 'Maya UK Autism Assistant (Educational/Non-commercial use)',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
            }
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.aclose()
    
    async def crawl_url(self, url: str, source: Source) -> Optional[CrawledDocument]:
        """Crawl a single URL and extract clean content"""
        try:
            logger.info(f"Crawling: {url}")
            
            # Rate limiting
            await asyncio.sleep(self.delay)
            
            response = await self.session.get(url)
            response.raise_for_status()
            
            # Extract content using trafilatura (removes navigation, ads, etc.)
            content = trafilatura.extract(
                response.text,
                include_comments=False,
                include_tables=True,
                include_links=False,
                favor_precision=True
            )
            
            if not content or len(content.strip()) < 100:
                logger.warning(f"Insufficient content extracted from {url}")
                return None
            
            # Parse with BeautifulSoup for additional metadata
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract title
            title = "Untitled"
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
            else:
                h1_tag = soup.find('h1')
                if h1_tag:
                    title = h1_tag.get_text().strip()
            
            # Extract section headers for better chunking
            headers = []
            for tag in soup.find_all(['h1', 'h2', 'h3', 'h4']):
                header_text = tag.get_text().strip()
                if header_text and len(header_text) < 200:
                    headers.append(header_text)
            
            # Create document
            doc = CrawledDocument(
                url=url,
                title=title,
                content=content.strip(),
                source_name=source.name,
                authority=source.authority,
                crawled_at=datetime.now(self.uk_timezone),
                word_count=len(content.split()),
                section_headers=headers,
                metadata={
                    'source_description': source.description,
                    'location_specific': source.location_specific,
                    'response_status': response.status_code,
                    'content_length': len(response.text)
                }
            )
            
            logger.info(f"Successfully crawled: {url} ({doc.word_count} words)")
            return doc
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error crawling {url}: {e.response.status_code}")
            return None
        except Exception as e:
            logger.error(f"Error crawling {url}: {str(e)}")
            return None
    
    async def crawl_source(self, source: Source) -> List[CrawledDocument]:
        """Crawl all URLs for a specific source"""
        documents = []
        
        for path in source.crawl_paths:
            url = source.base_url + path
            doc = await self.crawl_url(url, source)
            if doc:
                documents.append(doc)
        
        return documents
    
    async def crawl_all_sources(self) -> List[CrawledDocument]:
        """Crawl all configured UK autism sources"""
        all_documents = []
        
        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(self.max_concurrent)
        
        async def crawl_source_with_semaphore(source: Source):
            async with semaphore:
                return await self.crawl_source(source)
        
        # Crawl all sources concurrently
        tasks = [crawl_source_with_semaphore(source) for source in UK_SOURCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Flatten results
        for result in results:
            if isinstance(result, list):
                all_documents.extend(result)
            elif isinstance(result, Exception):
                logger.error(f"Source crawling failed: {result}")
        
        logger.info(f"Crawling complete: {len(all_documents)} documents extracted")
        return all_documents

def chunk_document(doc: CrawledDocument, chunk_size: int = 1000, overlap: int = 200) -> List[Dict[str, Any]]:
    """Split document into chunks for vector storage"""
    words = doc.content.split()
    chunks = []
    
    # If document is small, return as single chunk
    if len(words) <= chunk_size:
        return [{
            'text': doc.content,
            'metadata': {
                'url': doc.url,
                'title': doc.title,
                'source_name': doc.source_name,
                'authority': doc.authority.value,
                'crawled_at': doc.crawled_at.isoformat(),
                'chunk_index': 0,
                'total_chunks': 1,
                'location_specific': doc.metadata.get('location_specific', False)
            }
        }]
    
    # Create overlapping chunks
    start = 0
    chunk_index = 0
    
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunk_words = words[start:end]
        chunk_text = ' '.join(chunk_words)
        
        chunks.append({
            'text': chunk_text,
            'metadata': {
                'url': doc.url,
                'title': doc.title,
                'source_name': doc.source_name,
                'authority': doc.authority.value,
                'crawled_at': doc.crawled_at.isoformat(),
                'chunk_index': chunk_index,
                'total_chunks': -1,  # Will be set after all chunks created
                'location_specific': doc.metadata.get('location_specific', False)
            }
        })
        
        # Move start position with overlap
        start = end - overlap if end < len(words) else len(words)
        chunk_index += 1
    
    # Update total chunks count
    for chunk in chunks:
        chunk['metadata']['total_chunks'] = len(chunks)
    
    return chunks

async def crawl_and_chunk_all() -> List[Dict[str, Any]]:
    """Main function to crawl all sources and return chunked documents"""
    async with UKAutismCrawler() as crawler:
        documents = await crawler.crawl_all_sources()
    
    # Chunk all documents
    all_chunks = []
    for doc in documents:
        chunks = chunk_document(doc)
        all_chunks.extend(chunks)
    
    logger.info(f"Created {len(all_chunks)} chunks from {len(documents)} documents")
    return all_chunks