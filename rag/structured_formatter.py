"""
Structured Data Formatter
Formats bureaucratic guidance entries with rich metadata for user-friendly display
"""

import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class StructuredDataFormatter:
    """Format structured knowledge entries for presentation to users"""
    
    def format_result(self, result: Dict[str, Any]) -> str:
        """Format a single search result with rich structured metadata"""
        metadata = result.get('metadata', {})
        
        # Check if this is a structured entry
        if not metadata.get('is_structured'):
            # Regular crawled content - return plain text
            return result.get('text', '')
        
        # Format structured entry with rich details
        formatted_parts = []
        
        # Title and description
        title = metadata.get('title', 'Untitled')
        description = metadata.get('description_plain', '')
        formatted_parts.append(f"**{title}**\n{description}")
        
        # Steps summary (critical for bureaucratic processes)
        if metadata.get('steps_summary'):
            formatted_parts.append(f"\n**How to do this:**\n{metadata['steps_summary']}")
        
        # Eligibility
        if metadata.get('eligibility_summary'):
            formatted_parts.append(f"\n**Who can use this:**\n{metadata['eligibility_summary']}")
        
        # Evidence required
        if metadata.get('evidence_required'):
            formatted_parts.append(f"\n**Evidence you'll need:**\n{metadata['evidence_required']}")
        
        # Deadlines (very important!)
        if metadata.get('deadlines'):
            formatted_parts.append(f"\n**⏰ Important deadlines:**\n{metadata['deadlines']}")
        
        # Contacts
        contacts_json = metadata.get('contacts', '{}')
        try:
            contacts = json.loads(contacts_json) if isinstance(contacts_json, str) else contacts_json
            if contacts and not contacts.get('web_only'):
                contact_parts = []
                if contacts.get('phone'):
                    contact_parts.append(f"📞 Phone: {contacts['phone']}")
                if contacts.get('email'):
                    contact_parts.append(f"📧 Email: {contacts['email']}")
                if contacts.get('address'):
                    contact_parts.append(f"📍 Address: {contacts['address']}")
                
                if contact_parts:
                    formatted_parts.append(f"\n**Contact details:**\n" + "\n".join(contact_parts))
                    
                    # Add opening hours if available
                    opening_hours = metadata.get('opening_hours', '')
                    if opening_hours and opening_hours != 'N/A':
                        formatted_parts.append(f"Hours: {opening_hours}")
        except (json.JSONDecodeError, TypeError):
            pass
        
        # Legal basis (helpful for understanding rights)
        if metadata.get('legal_basis'):
            formatted_parts.append(f"\n**Legal basis:**\n{metadata['legal_basis']}")
        
        # Source and URL
        source_name = metadata.get('source_name', 'Unknown source')
        url = metadata.get('url', '')
        if url:
            formatted_parts.append(f"\n📚 **Source:** {source_name}\n🔗 **Link:** {url}")
        
        return "\n".join(formatted_parts)
    
    def format_results_for_synthesis(self, results: List[Dict[str, Any]]) -> str:
        """Format multiple results for LLM synthesis"""
        if not results:
            return ""
        
        formatted_sections = []
        
        for i, result in enumerate(results, 1):
            metadata = result.get('metadata', {})
            
            # Format each result with clear separation
            section_parts = [f"\n## Source {i}: {metadata.get('title', 'Untitled')}"]
            
            # Add content
            if metadata.get('is_structured'):
                # Structured entry - include rich details
                section_parts.append(self.format_result(result))
            else:
                # Regular crawled content
                section_parts.append(result.get('text', ''))
            
            formatted_sections.append("\n".join(section_parts))
        
        return "\n\n---\n".join(formatted_sections)
    
    def extract_actionable_info(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """Extract actionable information for Maya's instructions"""
        actionable = {}
        
        if metadata.get('steps_summary'):
            actionable['steps'] = metadata['steps_summary']
        
        if metadata.get('evidence_required'):
            actionable['evidence'] = metadata['evidence_required']
        
        if metadata.get('deadlines'):
            actionable['deadlines'] = metadata['deadlines']
        
        if metadata.get('contacts'):
            try:
                contacts_json = metadata['contacts']
                contacts = json.loads(contacts_json) if isinstance(contacts_json, str) else contacts_json
                if contacts and not contacts.get('web_only'):
                    actionable['contacts'] = contacts
            except (json.JSONDecodeError, TypeError):
                pass
        
        if metadata.get('notes_for_maya'):
            actionable['maya_notes'] = metadata['notes_for_maya']
        
        return actionable


def format_structured_results(results: List[Dict[str, Any]]) -> str:
    """Convenience function to format structured results"""
    formatter = StructuredDataFormatter()
    return formatter.format_results_for_synthesis(results)
