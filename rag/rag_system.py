"""
Complete RAG System for UK Autism Assistant
Orchestrates crawling, storage, retrieval, and response generation
"""

import asyncio
import logging
from typing import Dict, Any, Optional
import os
import time
from enum import Enum

from .crawler import crawl_and_chunk_all
from .vector_store import UKAutismVectorStore
from .llm_client import UKAutismLLMClient
from .retriever import UKAutismRetriever
from .citations import CitationFormatter

logger = logging.getLogger(__name__)

class InitializationState(Enum):
    NOT_STARTED = "not_started"
    INITIALIZING = "initializing"
    READY = "ready"
    ERROR = "error"

class UKAutismRAGSystem:
    def __init__(self):
        self.vector_store = None
        self.llm_client = None
        self.retriever = None
        self.citation_formatter = CitationFormatter()
        self.initialized = False
        self.init_state = InitializationState.NOT_STARTED
        self.init_error = None
        self._init_lock = asyncio.Lock()
    
    async def initialize_async(self):
        """Initialize all RAG components asynchronously in background"""
        async with self._init_lock:
            if self.init_state in [InitializationState.READY, InitializationState.INITIALIZING]:
                return
            
            self.init_state = InitializationState.INITIALIZING
            start_time = time.time()
            
            try:
                logger.info("🚀 Starting background RAG system initialization...")
                
                # Run heavy initialization in thread pool to avoid blocking
                await asyncio.to_thread(self._initialize_sync)
                
                elapsed = time.time() - start_time
                logger.info(f"✅ RAG System initialized successfully in {elapsed:.2f}s")
                
                self.init_state = InitializationState.READY
                self.initialized = True
                
            except Exception as e:
                elapsed = time.time() - start_time
                logger.error(f"❌ Failed to initialize RAG system after {elapsed:.2f}s: {str(e)}")
                self.init_state = InitializationState.ERROR
                self.init_error = str(e)
                self.initialized = False
    
    def _initialize_sync(self):
        """Synchronous initialization logic (runs in thread pool)"""
        logger.info("📦 Loading vector store and embeddings model...")
        vec_start = time.time()
        
        # Initialize vector store (loads ChromaDB + SentenceTransformer)
        self.vector_store = UKAutismVectorStore()
        self.vector_store.initialize()
        
        vec_elapsed = time.time() - vec_start
        logger.info(f"✓ Vector store loaded in {vec_elapsed:.2f}s")
        
        # Initialize LLM client (lightweight)
        logger.info("🤖 Initializing LLM client...")
        llm_start = time.time()
        self.llm_client = UKAutismLLMClient()
        llm_elapsed = time.time() - llm_start
        logger.info(f"✓ LLM client ready in {llm_elapsed:.2f}s")
        
        # Initialize retriever
        logger.info("🔍 Setting up retriever...")
        self.retriever = UKAutismRetriever(self.vector_store, self.llm_client)
        logger.info("✓ Retriever configured")
        
        # Check collection stats
        stats = self.vector_store.get_collection_stats()
        logger.info(f"📊 Knowledge base contains {stats.get('total_chunks', 0)} chunks")
    
    def initialize(self):
        """Legacy synchronous initialize - deprecated but kept for compatibility"""
        try:
            logger.info("Initializing UK Autism RAG System (sync mode)...")
            
            # Initialize vector store
            self.vector_store = UKAutismVectorStore()
            self.vector_store.initialize()
            
            # Initialize LLM client
            self.llm_client = UKAutismLLMClient()
            
            # Initialize retriever
            self.retriever = UKAutismRetriever(self.vector_store, self.llm_client)
            
            self.initialized = True
            self.init_state = InitializationState.READY
            logger.info("RAG System initialized successfully")
            
            # Check if we need to populate the vector store
            stats = self.vector_store.get_collection_stats()
            if stats.get("total_chunks", 0) == 0:
                logger.info("Vector store is empty, population recommended")
                return {"needs_population": True, "stats": stats}
            else:
                logger.info(f"Vector store contains {stats['total_chunks']} chunks")
                return {"needs_population": False, "stats": stats}
                
        except Exception as e:
            logger.error(f"Failed to initialize RAG system: {str(e)}")
            self.initialized = False
            self.init_state = InitializationState.ERROR
            self.init_error = str(e)
            raise
    
    async def populate_knowledge_base(self):
        """Crawl sources and populate the vector store"""
        if not self.vector_store:
            raise RuntimeError("Vector store not initialized")
        
        logger.info("Starting knowledge base population...")
        
        # Crawl all sources and create chunks
        chunks = await crawl_and_chunk_all()
        
        if chunks:
            # Add to vector store
            self.vector_store.add_documents(chunks)
            logger.info(f"Knowledge base populated with {len(chunks)} chunks")
            return {"success": True, "chunks_added": len(chunks)}
        else:
            logger.warning("No chunks were created from crawling")
            return {"success": False, "chunks_added": 0}
    
    def answer_question(self, user_question: str, comprehension_level: str = "standard") -> str:
        """
        Main entry point for answering questions using the full RAG pipeline
        
        Args:
            user_question: The user's question
            comprehension_level: Reading complexity level - "clear" (simple), "standard", or "complex" (detailed)
        """
        if not self.initialized:
            return "System not initialized. Please try again in a moment."
        
        try:
            # Apply safety guardrails first (import here to avoid circular dependency)
            from .answerer import apply_guardrails
            guardrail_response = apply_guardrails(user_question)
            if guardrail_response:
                return guardrail_response
            
            # Check content appropriateness
            appropriateness = self.llm_client.check_content_appropriateness(user_question)
            if not appropriateness.get("appropriate", True):
                return f"""I'm focused on providing information about autism in the UK. 

{appropriateness.get('reason', 'Your question seems to be outside my area of expertise.')}

I can help with questions about:
• Autism information and characteristics
• UK support services and benefits  
• Education and EHCP processes
• Local services in Hounslow
• Government guidance and NHS resources

Please feel free to ask me about any of these topics!"""
            
            # Retrieve relevant information
            retrieval_result = self.retriever.retrieve(user_question)
            
            if not retrieval_result["results"]:
                return self._handle_no_results(user_question)
            
            # Generate response using LLM with comprehension level
            llm_result = self.llm_client.synthesize_response(
                user_question, 
                retrieval_result["results"],
                comprehension_level=comprehension_level
            )
            
            if not llm_result["success"]:
                return self._handle_generation_error(user_question)
            
            # Format with citations
            response_with_citations = self._format_final_response(
                llm_result, 
                retrieval_result
            )
            
            return response_with_citations
            
        except Exception as e:
            logger.error(f"Error in answer_question: {str(e)}")
            return self._handle_system_error()
    
    def _handle_no_results(self, user_question: str) -> str:
        """Handle case when no relevant information is found"""
        return f"""I don't have specific information about that in my knowledge base yet.

For your question about: {user_question}

I'd recommend checking these trusted UK sources:
• **NHS**: nhs.uk/conditions/autism/
• **National Autistic Society**: autism.org.uk
• **Gov.UK**: gov.uk (search for autism or SEND guidance)
• **IPSEA**: ipsea.org.uk (for education and legal advice)

If you're in Hounslow, you can also contact:
• **Hounslow Council SEND team**: hounslow.gov.uk

{self.citation_formatter.create_source_disclaimer()}"""
    
    def _handle_generation_error(self, user_question: str) -> str:
        """Handle LLM generation errors"""
        return f"""I'm having trouble generating a response right now.

For your question about: {user_question}

Please try these trusted sources directly:
• **NHS**: nhs.uk/conditions/autism/
• **National Autistic Society**: autism.org.uk
• **Gov.UK SEND guidance**: gov.uk/special-educational-needs-support-council

{self.citation_formatter.create_source_disclaimer()}"""
    
    def _handle_system_error(self) -> str:
        """Handle system-level errors"""
        return """I'm experiencing technical difficulties right now. Please try again in a moment.

In the meantime, you can find reliable autism information at:
• **NHS**: nhs.uk/conditions/autism/
• **National Autistic Society**: autism.org.uk
• **Gov.UK**: gov.uk (search for autism support)

For urgent support, contact NHS 111 or your GP."""
    
    def _format_final_response(self, llm_result: Dict[str, Any], 
                             retrieval_result: Dict[str, Any]) -> str:
        """Format the final response with proper citations and transparency"""
        response = llm_result["response"]
        
        # Add source citations
        if llm_result["sources_used"]:
            citations = self.citation_formatter.format_sources(llm_result["sources_used"])
            if citations:
                response = f"{response}\n\n{citations}"
        
        # Add transparency note
        response = self.citation_formatter.add_transparency_note(
            response,
            llm_result["chunks_used"],
            llm_result["model_used"]
        )
        
        # Add disclaimer
        response = f"{response}\n{self.citation_formatter.create_source_disclaimer()}"
        
        return response
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get current system status and statistics"""
        if not self.initialized:
            return {"status": "not_initialized"}
        
        try:
            vector_stats = self.vector_store.get_collection_stats()
            
            return {
                "status": "operational",
                "initialized": self.initialized,
                "vector_store": vector_stats,
                "llm_client": "connected" if self.llm_client and self.llm_client.client else "disconnected"
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e)
            }

# Global instance
_rag_system = None

def get_rag_system() -> UKAutismRAGSystem:
    """Get or create the global RAG system instance"""
    global _rag_system
    if _rag_system is None:
        _rag_system = UKAutismRAGSystem()
    return _rag_system