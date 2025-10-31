"""
Structured Knowledge Importer
Imports curated JSONL/CSV datasets with rich metadata for bureaucratic processes
"""

import json
import csv
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class StructuredKnowledgeImporter:
    """Import structured knowledge from JSONL or CSV files"""
    
    REQUIRED_FIELDS = [
        'id', 'title', 'url', 'source_name', 'source_type', 'category',
        'subcategory', 'audience', 'age_range', 'locality', 'description_plain',
        'format_type', 'reliability_score'
    ]
    
    ENRICHMENT_FIELDS = [
        'steps_summary', 'eligibility_summary', 'evidence_required',
        'deadlines', 'contacts', 'opening_hours', 'legal_basis',
        'notes_for_maya'
    ]
    
    def __init__(self):
        self.entries = []
        self.validation_errors = []
    
    def load_jsonl(self, filepath: str) -> List[Dict[str, Any]]:
        """Load entries from JSONL file"""
        entries = []
        filepath = Path(filepath)
        
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        
        logger.info(f"Loading JSONL from {filepath}")
        
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError as e:
                    error_msg = f"Line {line_num}: Invalid JSON - {str(e)}"
                    logger.error(error_msg)
                    self.validation_errors.append(error_msg)
        
        logger.info(f"Loaded {len(entries)} entries from JSONL")
        return entries
    
    def load_csv(self, filepath: str) -> List[Dict[str, Any]]:
        """Load entries from CSV file"""
        entries = []
        filepath = Path(filepath)
        
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {filepath}")
        
        logger.info(f"Loading CSV from {filepath}")
        
        with open(filepath, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row_num, row in enumerate(reader, 1):
                try:
                    # Parse JSON fields that are stored as strings in CSV
                    if 'audience' in row and row['audience']:
                        row['audience'] = json.loads(row['audience'])
                    if 'contacts' in row and row['contacts']:
                        row['contacts'] = json.loads(row['contacts'])
                    if 'tags' in row and row['tags']:
                        row['tags'] = json.loads(row['tags'])
                    
                    # Convert reliability_score to int
                    if 'reliability_score' in row:
                        row['reliability_score'] = int(row['reliability_score'])
                    
                    # Convert booleans
                    if 'pdf_available' in row:
                        row['pdf_available'] = row['pdf_available'].lower() == 'true'
                    
                    entries.append(row)
                except (json.JSONDecodeError, ValueError) as e:
                    error_msg = f"Row {row_num}: Parse error - {str(e)}"
                    logger.error(error_msg)
                    self.validation_errors.append(error_msg)
        
        logger.info(f"Loaded {len(entries)} entries from CSV")
        return entries
    
    def validate_entry(self, entry: Dict[str, Any]) -> bool:
        """Validate a single entry has required fields"""
        entry_id = entry.get('id', 'unknown')
        is_valid = True
        
        # Check required fields
        for field in self.REQUIRED_FIELDS:
            if field not in entry or not entry[field]:
                error_msg = f"Entry {entry_id}: Missing required field '{field}'"
                logger.warning(error_msg)
                self.validation_errors.append(error_msg)
                is_valid = False
        
        # Validate reliability_score range
        if 'reliability_score' in entry:
            score = entry['reliability_score']
            if not isinstance(score, int) or score < 1 or score > 5:
                error_msg = f"Entry {entry_id}: reliability_score must be 1-5, got {score}"
                logger.warning(error_msg)
                self.validation_errors.append(error_msg)
                is_valid = False
        
        return is_valid
    
    def convert_to_chunks(self, entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convert structured entries to chunks for vector storage"""
        chunks = []
        
        for entry in entries:
            # Validate entry
            if not self.validate_entry(entry):
                logger.warning(f"Skipping invalid entry: {entry.get('id', 'unknown')}")
                continue
            
            # Build rich text representation for embedding
            text_parts = [
                f"# {entry['title']}",
                f"\n{entry['description_plain']}\n"
            ]
            
            # Add structured details
            if entry.get('steps_summary'):
                text_parts.append(f"\n**How to do this:**\n{entry['steps_summary']}\n")
            
            if entry.get('eligibility_summary'):
                text_parts.append(f"\n**Who can use this:**\n{entry['eligibility_summary']}\n")
            
            if entry.get('evidence_required'):
                text_parts.append(f"\n**Evidence needed:**\n{entry['evidence_required']}\n")
            
            if entry.get('deadlines'):
                text_parts.append(f"\n**Important deadlines:**\n{entry['deadlines']}\n")
            
            if entry.get('legal_basis'):
                text_parts.append(f"\n**Legal basis:**\n{entry['legal_basis']}\n")
            
            # Add content excerpt if available
            if entry.get('content_excerpt'):
                text_parts.append(f"\n{entry['content_excerpt']}\n")
            
            chunk_text = "\n".join(text_parts)
            
            # Create metadata (preserve ALL structured fields)
            metadata = {
                'entry_id': entry['id'],
                'url': entry['url'],
                'title': entry['title'],
                'source_name': entry['source_name'],
                'source_type': entry['source_type'],
                'authority': self._map_source_to_authority(entry['source_type']),
                'category': entry['category'],
                'subcategory': entry['subcategory'],
                'locality': entry['locality'],
                'format_type': entry['format_type'],
                'reliability_score': entry['reliability_score'],
                'is_structured': True,  # Flag to identify structured entries
                'chunk_index': 0,
                'total_chunks': 1,
                'location_specific': entry['locality'] in ['hounslow', 'west_london'],
                'crawled_at': datetime.now().isoformat(),
                
                # Store enrichment fields as JSON strings for ChromaDB
                'steps_summary': entry.get('steps_summary', ''),
                'eligibility_summary': entry.get('eligibility_summary', ''),
                'evidence_required': entry.get('evidence_required', ''),
                'deadlines': entry.get('deadlines', ''),
                'contacts': json.dumps(entry.get('contacts', {})),
                'opening_hours': entry.get('opening_hours', ''),
                'legal_basis': entry.get('legal_basis', ''),
                'notes_for_maya': entry.get('notes_for_maya', ''),
                'audience': json.dumps(entry.get('audience', [])),
                'age_range': entry.get('age_range', ''),
            }
            
            chunk = {
                'text': chunk_text,
                'metadata': metadata
            }
            
            chunks.append(chunk)
        
        logger.info(f"Converted {len(chunks)} entries to chunks")
        return chunks
    
    def _map_source_to_authority(self, source_type: str) -> int:
        """Map source_type to authority level for compatibility"""
        authority_map = {
            'government': 1,
            'nhs': 2,
            'local_authority': 2,
            'charity': 3,
            'community_org': 4,
            'legal_advice': 3
        }
        return authority_map.get(source_type, 3)
    
    def import_file(self, filepath: str) -> List[Dict[str, Any]]:
        """Import from JSONL or CSV file and return chunks"""
        filepath = Path(filepath)
        
        if filepath.suffix == '.jsonl':
            entries = self.load_jsonl(filepath)
        elif filepath.suffix == '.csv':
            entries = self.load_csv(filepath)
        else:
            raise ValueError(f"Unsupported file type: {filepath.suffix}. Use .jsonl or .csv")
        
        if not entries:
            logger.warning("No entries loaded from file")
            return []
        
        # Convert to chunks
        chunks = self.convert_to_chunks(entries)
        
        # Report validation errors
        if self.validation_errors:
            logger.warning(f"Import completed with {len(self.validation_errors)} validation warnings")
            for error in self.validation_errors[:10]:  # Show first 10
                logger.warning(f"  - {error}")
            if len(self.validation_errors) > 10:
                logger.warning(f"  ... and {len(self.validation_errors) - 10} more")
        
        return chunks


def import_structured_knowledge(filepath: str) -> List[Dict[str, Any]]:
    """
    Convenience function to import structured knowledge from a file
    
    Args:
        filepath: Path to .jsonl or .csv file
    
    Returns:
        List of chunks ready for vector store
    """
    importer = StructuredKnowledgeImporter()
    return importer.import_file(filepath)
