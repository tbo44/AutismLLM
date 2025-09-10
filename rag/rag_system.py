"""
Complete RAG System for UK Autism Assistant
Orchestrates crawling, storage, retrieval, and response generation
"""

import asyncio
import logging
from typing import Dict, Any, Optional
import os

from .crawler import crawl_and_chunk_all
from .vector_store import UKAutismVectorStore
from .llm_client import UKAutismLLMClient
from .retriever import UKAutismRetriever
from .citations import CitationFormatter
from .answerer import apply_guardrails

logger = logging.getLogger(__name__)

class UKAutismRAGSystem:
    def __init__(self):
        self.vector_store = None
        self.llm_client = None
        self.retriever = None
        self.citation_formatter = CitationFormatter()
        self.initialized = False
    
    def initialize(self):
        """Initialize all RAG components"""
        try:
            logger.info("Initializing UK Autism RAG System...")
            
            # Initialize vector store
            self.vector_store = UKAutismVectorStore()
            self.vector_store.initialize()
            
            # Initialize LLM client
            self.llm_client = UKAutismLLMClient()
            
            # Initialize retriever
            self.retriever = UKAutismRetriever(self.vector_store, self.llm_client)
            
            self.initialized = True
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
    
    def answer_question(self, user_question: str) -> str:
        """
        Main entry point for answering questions using the full RAG pipeline
        """
        if not self.initialized:
            return "System not initialized. Please try again in a moment."
        
        try:
            # Apply safety guardrails first
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
            
            # Generate response using LLM
            llm_result = self.llm_client.synthesize_response(
                user_question, 
                retrieval_result["results"]
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