#!/usr/bin/env python3
"""
Import Structured Knowledge from JSONL/CSV
Imports curated bureaucratic guidance with rich metadata into the RAG system
"""
import asyncio
import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.rag_system import get_rag_system
from rag.structured_importer import import_structured_knowledge

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def main():
    """Main function to import structured knowledge"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Import structured knowledge from JSONL or CSV file'
    )
    parser.add_argument(
        'filepath',
        help='Path to JSONL or CSV file to import'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Validate file without importing to database'
    )
    
    args = parser.parse_args()
    
    logger.info("=" * 70)
    logger.info("Structured Knowledge Import Tool")
    logger.info("=" * 70)
    logger.info(f"File: {args.filepath}")
    logger.info(f"Mode: {'Dry run (validation only)' if args.dry_run else 'Full import'}")
    logger.info("")
    
    try:
        # Import and convert structured data
        logger.info("📥 Loading structured knowledge...")
        chunks = import_structured_knowledge(args.filepath)
        
        if not chunks:
            logger.error("❌ No valid entries found in file")
            return 1
        
        logger.info(f"✅ Loaded {len(chunks)} entries successfully")
        
        # Show sample of what will be imported
        logger.info("\n" + "=" * 70)
        logger.info("Sample Entry:")
        logger.info("=" * 70)
        sample = chunks[0]
        logger.info(f"Title: {sample['metadata']['title']}")
        logger.info(f"Source: {sample['metadata']['source_name']}")
        logger.info(f"Category: {sample['metadata']['category']}")
        logger.info(f"Locality: {sample['metadata']['locality']}")
        if sample['metadata'].get('steps_summary'):
            logger.info(f"Has steps: Yes")
        if sample['metadata'].get('deadlines'):
            logger.info(f"Has deadlines: Yes")
        if sample['metadata'].get('contacts'):
            logger.info(f"Has contacts: Yes")
        logger.info("")
        
        if args.dry_run:
            logger.info("=" * 70)
            logger.info("✅ DRY RUN COMPLETE - File validated successfully")
            logger.info(f"Ready to import {len(chunks)} entries")
            logger.info("=" * 70)
            logger.info("\nRun without --dry-run to import to database")
            return 0
        
        # Initialize RAG system
        logger.info("🚀 Initializing RAG system...")
        rag_system = get_rag_system()
        rag_system.initialize()
        
        # Add chunks to vector store
        logger.info(f"\n📚 Adding {len(chunks)} entries to knowledge base...")
        rag_system.vector_store.add_documents(chunks)
        
        logger.info("\n" + "=" * 70)
        logger.info("✅ IMPORT COMPLETE!")
        logger.info("=" * 70)
        logger.info(f"Successfully added {len(chunks)} structured entries")
        logger.info("\nThe knowledge base has been updated with:")
        logger.info("  • Step-by-step guides")
        logger.info("  • Contact details")
        logger.info("  • Important deadlines")
        logger.info("  • Evidence requirements")
        logger.info("  • Legal basis information")
        logger.info("\nRestart your server to use the new knowledge!")
        
        return 0
        
    except FileNotFoundError as e:
        logger.error(f"❌ File not found: {e}")
        return 1
    except Exception as e:
        logger.error(f"❌ Error during import: {str(e)}", exc_info=True)
        return 1

if __name__ == "__main__":
    sys.exit(main())
