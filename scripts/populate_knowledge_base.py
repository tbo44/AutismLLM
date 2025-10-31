#!/usr/bin/env python3
"""
Script to populate the knowledge base by crawling UK autism sources
Run this whenever you add new sources to rag/sources.py
"""
import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path so we can import rag modules
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.rag_system import get_rag_system

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

async def main():
    """Main function to populate knowledge base"""
    logger.info("=" * 60)
    logger.info("Starting Knowledge Base Population")
    logger.info("=" * 60)
    
    try:
        # Initialize RAG system
        logger.info("Initializing RAG system...")
        rag_system = get_rag_system()
        rag_system.initialize()
        
        # Populate knowledge base
        logger.info("\nCrawling sources and adding documents...")
        result = await rag_system.populate_knowledge_base()
        
        if result.get('success'):
            logger.info("\n" + "=" * 60)
            logger.info(f"✅ SUCCESS! Added {result.get('chunks_added')} document chunks")
            logger.info("=" * 60)
            logger.info("\nYour knowledge base has been updated!")
            logger.info("The changes will be available after restarting your server.")
        else:
            logger.warning("\n⚠️ No documents were added. Check the logs above for details.")
            
    except Exception as e:
        logger.error(f"\n❌ Error during population: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
