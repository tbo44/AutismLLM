"""
Intelligent Retrieval System for UK Autism Assistant
Handles query enhancement, retrieval, and result ranking
"""

from typing import List, Dict, Any, Optional
import logging
import re

from .vector_store import UKAutismVectorStore
from .llm_client import UKAutismLLMClient

logger = logging.getLogger(__name__)

class UKAutismRetriever:
    def __init__(self, vector_store: UKAutismVectorStore, llm_client: UKAutismLLMClient):
        self.vector_store = vector_store
        self.llm_client = llm_client
    
    def retrieve(self, user_question: str, max_results: int = 8) -> Dict[str, Any]:
        """
        Main retrieval method with intelligent query enhancement and ranking
        """
        try:
            # Check if question is Hounslow-specific
            hounslow_specific = self._is_hounslow_query(user_question)
            
            # Enhance query for better retrieval
            enhanced_query = self.llm_client.enhance_query(user_question)
            
            # Perform vector search
            if hounslow_specific:
                # First try Hounslow-specific sources
                local_results = self.vector_store.search(
                    enhanced_query, 
                    n_results=max_results // 2,
                    hounslow_specific=True
                )
                
                # Then get general results
                general_results = self.vector_store.search(
                    enhanced_query,
                    n_results=max_results - len(local_results),
                    hounslow_specific=False
                )
                
                # Combine with local results prioritized
                results = local_results + general_results
            else:
                # Standard search with authority ranking
                results = self.vector_store.search(
                    enhanced_query,
                    n_results=max_results,
                    authority_boost=True
                )
            
            # Apply additional filtering and ranking
            filtered_results = self._filter_and_rank_results(results, user_question)
            
            return {
                "results": filtered_results,
                "enhanced_query": enhanced_query,
                "hounslow_specific": hounslow_specific,
                "total_found": len(results),
                "total_returned": len(filtered_results)
            }
            
        except Exception as e:
            logger.error(f"Retrieval error: {str(e)}")
            return {
                "results": [],
                "enhanced_query": user_question,
                "hounslow_specific": False,
                "total_found": 0,
                "total_returned": 0,
                "error": str(e)
            }
    
    def _is_hounslow_query(self, question: str) -> bool:
        """Check if question specifically asks about Hounslow"""
        hounslow_terms = [
            "hounslow", "local", "near me", "my area", "my council", 
            "local authority", "local services", "tw3", "tw4", "tw5"
        ]
        
        question_lower = question.lower()
        return any(term in question_lower for term in hounslow_terms)
    
    def _filter_and_rank_results(self, results: List[Dict[str, Any]], 
                                query: str) -> List[Dict[str, Any]]:
        """Apply additional filtering and ranking logic"""
        if not results:
            return results
        
        # Filter out very low quality matches
        min_relevance_threshold = 0.8  # Adjust based on testing
        filtered = [r for r in results if r['distance'] < min_relevance_threshold]
        
        # If too few results after filtering, include more
        if len(filtered) < 3 and len(results) > 0:
            filtered = results[:6]  # Take top 6 regardless of threshold
        
        # Apply query-specific ranking adjustments
        for result in filtered:
            result['relevance_score'] = self._calculate_relevance_score(result, query)
        
        # Sort by relevance score (higher is better)
        filtered.sort(key=lambda x: x['relevance_score'], reverse=True)
        
        return filtered
    
    def _calculate_relevance_score(self, result: Dict[str, Any], query: str) -> float:
        """Calculate relevance score combining distance and other factors"""
        base_score = 1.0 - result['distance']  # Higher for closer matches
        
        metadata = result['metadata']
        
        # Authority boost (government and NHS sources get priority)
        authority = int(metadata['authority'])
        authority_boost = (6 - authority) * 0.1  # 0.5 for gov, 0.4 for NHS, etc.
        
        # Recency boost (more recent content gets slight priority)
        # This would require parsing crawled_at timestamp, simplified for now
        recency_boost = 0.0
        
        # Content quality boost (longer, well-structured content)
        content_length = len(result['text'])
        length_boost = min(content_length / 1000, 0.1)  # Up to 0.1 boost
        
        # Query-specific boost for exact term matches
        query_terms = query.lower().split()
        text_lower = result['text'].lower()
        title_lower = metadata['title'].lower()
        
        term_match_boost = 0.0
        for term in query_terms:
            if len(term) > 3:  # Only boost for meaningful terms
                if term in title_lower:
                    term_match_boost += 0.05
                elif term in text_lower:
                    term_match_boost += 0.02
        
        final_score = base_score + authority_boost + recency_boost + length_boost + term_match_boost
        
        return min(final_score, 1.0)  # Cap at 1.0