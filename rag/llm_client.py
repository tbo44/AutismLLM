"""
Groq LLM Client for UK Autism Assistant
Handles natural language generation with safety guardrails using open-source models
"""

import os
import json
import logging
from typing import List, Dict, Any, Optional
from groq import Groq

from .structured_formatter import StructuredDataFormatter

logger = logging.getLogger(__name__)

class UKAutismLLMClient:
    def __init__(self):
        self.client = None
        # Use configurable Groq model with stable default
        self.model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        self.formatter = StructuredDataFormatter()
        self._initialize_client()
    
    def _initialize_client(self):
        """Initialize Groq client"""
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable not found")
        
        self.client = Groq(api_key=api_key)
        logger.info("Groq client initialized successfully")
    
    def synthesize_response(self, user_question: str, retrieved_chunks: List[Dict[str, Any]], 
                          context: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate response using retrieved information with UK autism focus
        """
        if not self.client:
            return {
                "response": "LLM client not available",
                "sources_used": [],
                "chunks_used": 0,
                "model_used": self.model,
                "success": False,
                "error": "Client not initialized"
            }
        
        try:
            # Build context from retrieved chunks using structured formatter
            sources_used = set()
            
            # Format context with rich structured data presentation
            context_text = self.formatter.format_results_for_synthesis(retrieved_chunks)
            
            # Collect sources
            for chunk in retrieved_chunks:
                metadata = chunk['metadata']
                sources_used.add((metadata['source_name'], metadata['url'], metadata['title']))
            
            # Build system prompt for UK autism assistant
            system_prompt = """You are Maya, a UK autism facts assistant. You provide helpful, accurate information about autism in the UK context.

IMPORTANT GUIDELINES:
1. Focus specifically on UK information, services, and legislation
2. Use the provided context information to answer questions
3. Always cite your sources clearly
4. If asked about Hounslow specifically, prioritize local information
5. Be supportive and use person-first or identity-first language as appropriate
6. Never provide medical diagnoses, treatment plans, or medication advice
7. Never provide specific legal advice for individual cases
8. If someone appears in crisis, direct them to appropriate services

WHEN CONTEXT INCLUDES STRUCTURED GUIDANCE (steps, deadlines, contacts):
- Present the step-by-step instructions clearly
- Emphasize important deadlines prominently
- Include contact details (phone, email, address, opening hours)
- List evidence requirements
- Explain legal basis when relevant
- Guide the user through the process systematically

RESPONSE FORMAT:
- Provide a clear, helpful answer based on the context
- Include inline citations like [NHS] or [National Autistic Society]
- For bureaucratic processes, structure your answer with clear steps
- End with a "Sources:" section listing the references used
- Keep language accessible and supportive

CONTEXT INFORMATION:
{context}
"""

            user_prompt = f"""Question: {user_question}

Please provide a helpful response based on the context information above. Remember to focus on UK-specific information and cite your sources."""

            # Generate response
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt.format(context=context_text)},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,  # Lower temperature for more factual responses
                max_tokens=1000
            )
            
            generated_text = response.choices[0].message.content
            
            return {
                "response": generated_text,
                "sources_used": list(sources_used),
                "chunks_used": len(retrieved_chunks),
                "model_used": self.model,
                "success": True
            }
            
        except Exception as e:
            logger.error(f"Error generating response: {str(e)}")
            return {
                "response": "I'm sorry, I'm having trouble generating a response right now. Please try again in a moment.",
                "sources_used": [],
                "chunks_used": 0,
                "model_used": self.model,
                "success": False,
                "error": str(e)
            }
    
    def enhance_query(self, user_question: str) -> str:
        """
        Enhance user query for better retrieval
        """
        if not self.client:
            return user_question
            
        try:
            enhancement_prompt = """You are helping to improve search queries for a UK autism information system.

Given a user's question, rewrite it to be more specific and likely to find relevant information about autism in the UK context.

Focus on:
- UK-specific terms and services
- Clear autism-related concepts
- Government services, benefits, education terms
- NHS and local authority services

Original question: {question}

Return only the enhanced query, nothing else."""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": enhancement_prompt.format(question=user_question)}
                ],
                temperature=0.1,
                max_tokens=100
            )
            
            enhanced_query = response.choices[0].message.content
            if enhanced_query:
                enhanced_query = enhanced_query.strip()
                logger.info(f"Enhanced query: '{user_question}' -> '{enhanced_query}'")
            return enhanced_query
            
        except Exception as e:
            logger.error(f"Error enhancing query: {str(e)}")
            return user_question  # Return original on error
    
    def check_content_appropriateness(self, user_question: str) -> Dict[str, Any]:
        """
        Check if content is appropriate for the autism assistant
        """
        if not self.client:
            return {"appropriate": True, "reason": "Moderation unavailable", "category": "unknown"}
            
        try:
            moderation_prompt = """You are checking if a question is appropriate for a UK autism facts assistant.

The assistant should answer questions about:
- Autism information and support
- UK services and benefits
- Education and EHCP processes
- General autism characteristics and support strategies

The assistant should NOT answer:
- Questions completely unrelated to autism
- Requests for entertainment (jokes, games, stories) unless autism-related
- General medical questions not related to autism
- Technical questions about other topics

Question: {question}

Respond with JSON in this format:
{{"appropriate": true/false, "reason": "explanation", "category": "autism-related/off-topic/other"}}"""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": moderation_prompt.format(question=user_question)}
                ],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            
            content = response.choices[0].message.content
            if content:
                result = json.loads(content)
                return result
            else:
                return {"appropriate": True, "reason": "No content returned", "category": "unknown"}
            
        except Exception as e:
            logger.error(f"Error checking content appropriateness: {str(e)}")
            return {"appropriate": True, "reason": "Moderation check failed", "category": "unknown"}