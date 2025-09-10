"""
ChromaDB Vector Store for UK Autism Knowledge Base
Handles embeddings, storage, and retrieval with authority-based ranking
"""

import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer
import os
import logging
from typing import List, Dict, Any, Optional
import json
from datetime import datetime
import asyncio

from .sources import SourceAuthority

logger = logging.getLogger(__name__)

class UKAutismVectorStore:
    def __init__(self, persist_directory: str = "./chroma_db"):
        self.persist_directory = persist_directory
        self.client = None
        self.collection = None
        self.embedder = None
        self.collection_name = "uk_autism_knowledge"
        
    def initialize(self):
        """Initialize ChromaDB and sentence transformer"""
        logger.info("Initializing vector store...")
        
        # Initialize ChromaDB with persistence
        self.client = chromadb.PersistentClient(
            path=self.persist_directory,
            settings=Settings(
                allow_reset=True,
                anonymized_telemetry=False
            )
        )
        
        # Initialize sentence transformer for embeddings
        # Using a model optimized for semantic search
        self.embedder = SentenceTransformer('all-MiniLM-L6-v2')
        
        # Create ChromaDB embedding function class
        class SentenceTransformerEmbeddingFunction:
            def __init__(self, model):
                self.model = model
                
            def __call__(self, input):
                # Handle both single text and list of texts
                if isinstance(input, str):
                    input = [input]
                return self.model.encode(input).tolist()
        
        embedding_function = SentenceTransformerEmbeddingFunction(self.embedder)
        
        # Get or create collection with embedding function
        try:
            self.collection = self.client.get_collection(
                name=self.collection_name,
                embedding_function=embedding_function
            )
            logger.info(f"Loaded existing collection: {self.collection_name}")
        except Exception:
            # Collection doesn't exist, create it
            self.collection = self.client.create_collection(
                name=self.collection_name,
                embedding_function=embedding_function,
                metadata={"description": "UK Autism Facts Knowledge Base"}
            )
            logger.info(f"Created new collection: {self.collection_name}")
    
    def add_documents(self, chunks: List[Dict[str, Any]]):
        """Add document chunks to the vector store"""
        if not chunks:
            logger.warning("No chunks provided to add")
            return
        
        logger.info(f"Adding {len(chunks)} chunks to vector store...")
        
        # Prepare data for ChromaDB
        ids = []
        documents = []
        metadatas = []
        
        for i, chunk in enumerate(chunks):
            # Create unique ID
            chunk_id = f"{chunk['metadata']['source_name']}_{chunk['metadata']['chunk_index']}_{i}"
            ids.append(chunk_id)
            
            # Document text
            documents.append(chunk['text'])
            
            # Metadata (ChromaDB requires string values)
            metadata = {
                'url': chunk['metadata']['url'],
                'title': chunk['metadata']['title'],
                'source_name': chunk['metadata']['source_name'],
                'authority': str(chunk['metadata']['authority']),
                'crawled_at': chunk['metadata']['crawled_at'],
                'chunk_index': str(chunk['metadata']['chunk_index']),
                'total_chunks': str(chunk['metadata']['total_chunks']),
                'location_specific': str(chunk['metadata']['location_specific'])
            }
            metadatas.append(metadata)
        
        # Add to collection in batches
        batch_size = 100
        for i in range(0, len(chunks), batch_size):
            end_idx = min(i + batch_size, len(chunks))
            
            if self.collection:
                self.collection.add(
                    ids=ids[i:end_idx],
                    documents=documents[i:end_idx],
                    metadatas=metadatas[i:end_idx]
                )
            
            logger.info(f"Added batch {i//batch_size + 1}/{(len(chunks) + batch_size - 1)//batch_size}")
        
        logger.info(f"Successfully added all {len(chunks)} chunks to vector store")
    
    def search(self, query: str, n_results: int = 10, authority_boost: bool = True, 
               hounslow_specific: bool = False) -> List[Dict[str, Any]]:
        """
        Search the vector store with authority-based ranking
        """
        if not self.collection:
            logger.error("Vector store not initialized")
            return []
        
        # Base search
        where_filter = {}
        if hounslow_specific:
            where_filter["location_specific"] = "True"
        
        try:
            if not self.collection:
                logger.error("Collection not available")
                return []
                
            results = self.collection.query(
                query_texts=[query],
                n_results=min(n_results * 2, 50) if authority_boost else n_results,  # Get more for reranking
                where=where_filter if where_filter else None
            )
            
            if not results or not results.get('documents') or not results['documents'][0]:
                logger.info("No results found for query")
                return []
            
            # Convert to structured format
            search_results = []
            for i in range(len(results['documents'][0])):
                result = {
                    'text': results['documents'][0][i],
                    'metadata': results['metadatas'][0][i],
                    'distance': results['distances'][0][i],
                    'id': results['ids'][0][i]
                }
                search_results.append(result)
            
            # Apply authority-based reranking if enabled
            if authority_boost:
                search_results = self._rerank_by_authority(search_results)
            
            # Return top n_results
            return search_results[:n_results]
            
        except Exception as e:
            logger.error(f"Search error: {str(e)}")
            return []
    
    def _rerank_by_authority(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Rerank results by source authority"""
        def authority_score(result):
            authority = int(result['metadata']['authority'])
            distance = result['distance']
            
            # Lower authority number = higher authority
            # Lower distance = better match
            # Combine with authority boost
            authority_boost = (6 - authority) * 0.1  # 0.5 for gov, 0.4 for NHS, etc.
            adjusted_distance = distance - authority_boost
            
            return adjusted_distance
        
        # Sort by adjusted distance (lower is better)
        results.sort(key=authority_score)
        return results
    
    def get_collection_stats(self) -> Dict[str, Any]:
        """Get statistics about the collection"""
        if not self.collection:
            return {"error": "Collection not initialized"}
        
        try:
            count = self.collection.count()
            
            # Get sample of metadata to analyze sources
            sample_results = self.collection.get(limit=min(count, 100))
            
            source_counts = {}
            authority_counts = {}
            
            for metadata in sample_results['metadatas']:
                source = metadata['source_name']
                authority = metadata['authority']
                
                source_counts[source] = source_counts.get(source, 0) + 1
                authority_counts[authority] = authority_counts.get(authority, 0) + 1
            
            return {
                "total_chunks": count,
                "sources": source_counts,
                "authorities": authority_counts,
                "collection_name": self.collection_name
            }
            
        except Exception as e:
            logger.error(f"Error getting collection stats: {str(e)}")
            return {"error": str(e)}
    
    def reset_collection(self):
        """Reset/clear the collection"""
        if self.client and self.collection:
            try:
                self.client.delete_collection(self.collection_name)
                logger.info(f"Deleted collection: {self.collection_name}")
                
                # Recreate
                self.initialize()
                
            except Exception as e:
                logger.error(f"Error resetting collection: {str(e)}")