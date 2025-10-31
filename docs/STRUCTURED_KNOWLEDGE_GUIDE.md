# Structured Knowledge Import Guide

## Overview

Maya now supports **two types of knowledge sources**:

1. **Web Crawling** - Crawls UK autism websites and extracts general information
2. **Structured Import** - Imports curated bureaucratic guides with rich metadata (NEW!)

The structured import system is specifically designed for **bureaucratic processes** where users need:
- Step-by-step instructions
- Contact details (phone, email, address)
- Important deadlines
- Evidence requirements
- Legal basis/rights

## Benefits of Structured Knowledge

Compared to web crawling, structured knowledge provides:

✅ **Actionable guidance** - Clear steps for complex processes  
✅ **Critical details** - Deadlines, contacts, evidence requirements  
✅ **Higher reliability** - Manually curated and verified  
✅ **Better UX** - Rich formatting with all essential information  
✅ **Faster retrieval** - Optimized metadata for bureaucratic queries  

## File Format

Structured knowledge uses **JSONL** (JSON Lines) or **CSV** format with a rich schema.

### Required Fields

- `id` - Unique identifier (e.g., "maya_hounslow_pip_mandatory_reconsideration")
- `title` - Clear, descriptive title
- `url` - Source URL
- `source_name` - Organization name
- `source_type` - One of: government, nhs, local_authority, charity, community_org, legal_advice
- `category` - One of: benefits, education_send, adult_social_care, appeals_complaints, employment_skills, travel_access, health_mental_health, community_daily_living, carer_support
- `subcategory` - Specific topic (e.g., "PIP", "EHCP", "Blue Badge")
- `audience` - Array: autistic_adult, parent_carer, young_person, professional
- `age_range` - One of: child_u16, young_person_16_25, adult_18plus, all_ages
- `locality` - One of: hounslow, west_london, national_uk
- `description_plain` - 1-3 sentence plain English description
- `format_type` - One of: step_by_step, template_letter, checklist, flowchart, guide, eligibility, timetable_deadlines, contact_directory
- `reliability_score` - Integer 1-5 (5=most reliable)

### Enrichment Fields (Optional but Recommended)

- `steps_summary` - Numbered steps (critical for processes!)
- `eligibility_summary` - Who can use this
- `evidence_required` - What documents/proof needed
- `deadlines` - Important time limits
- `contacts` - JSON object with phone, email, address
- `opening_hours` - When to contact
- `legal_basis` - Relevant laws/regulations
- `notes_for_maya` - Instructions for the AI assistant

## Usage

### 1. Create Your Knowledge File

Create a `.jsonl` file with one entry per line:

```jsonl
{"id":"maya_hounslow_pip_mr","title":"PIP: Mandatory Reconsideration","url":"https://...","source_name":"National Autistic Society","source_type":"charity","category":"benefits","subcategory":"PIP","audience":["autistic_adult"],"age_range":"adult_18plus","locality":"national_uk","description_plain":"How to challenge a PIP decision","steps_summary":"1. Read decision letter\n2. Write to DWP within 1 month...","eligibility_summary":"Must have PIP decision within 1 month","evidence_required":"decision letter, medical evidence, daily living diary","deadlines":"1 month from decision date","contacts":{"web_only":true},"opening_hours":"N/A","legal_basis":"PIP Regulations 2013","format_type":"step_by_step","pdf_available":false,"filetype":"webpage","tags":["pip","appeal"],"notes_for_maya":"Emphasize 1-month deadline","reliability_score":4,"last_verified_date":"2025-10-31","update_cycle":"annual","breadcrumb":"Benefits › PIP","license":"unspecified","content_excerpt":"Guide to MR process...","sha256":null,"duplicate_of":null}
```

### 2. Validate (Dry Run)

Test your file without importing to database:

```bash
python scripts/import_structured_knowledge.py data/your_file.jsonl --dry-run
```

This will:
- Check file format
- Validate required fields
- Show preview of what will be imported
- Report any errors

### 3. Import to Knowledge Base

Once validated, import for real:

```bash
python scripts/import_structured_knowledge.py data/your_file.jsonl
```

This will:
- Load and validate entries
- Convert to vector store format
- Embed with SentenceTransformers
- Store in ChromaDB with full metadata
- Report success/errors

### 4. Restart Server

After importing, restart your workflow:

```bash
# The new knowledge is immediately available after restart
```

## How It Works

### Import Process

1. **Load** - Parse JSONL/CSV file
2. **Validate** - Check required fields, data types
3. **Convert** - Transform to rich text chunks with metadata
4. **Embed** - Generate semantic embeddings
5. **Store** - Save to vector database

### Retrieval Enhancement

Structured entries get special treatment:

- **Boosted ranking** - +0.15 relevance score
- **Rich formatting** - Steps, contacts, deadlines displayed prominently
- **Priority matching** - Favored for bureaucratic queries

### Answer Synthesis

When Maya retrieves structured entries, she will:

1. Present clear step-by-step instructions
2. Highlight critical deadlines
3. Provide contact information
4. List evidence requirements
5. Cite legal basis

## Examples

### Sample Entry: PIP Mandatory Reconsideration

See `data/sample_structured_knowledge.jsonl` for complete examples including:
- PIP Mandatory Reconsideration guide
- EHCP Assessment process (Hounslow-specific)
- Blue Badge application (Hounslow-specific)

### Recommended Topics for Structured Import

**Benefits & Money:**
- PIP application, mandatory reconsideration, tribunal
- DLA application and appeals
- Universal Credit LCWRA
- Carer's Allowance
- Council Tax Reduction
- Discretionary support schemes

**Education (SEND):**
- EHCP request process
- EHCP appeals and mediation
- SEN support at school
- Post-16 education
- Transport to school

**Adult Social Care:**
- Care Act assessments
- Direct Payments
- Carer's assessments
- Respite care

**Appeals & Complaints:**
- SEND Tribunal
- Benefits appeals
- NHS complaints
- Local Government Ombudsman

## Best Practices

### Do:
✅ Verify all URLs are live (200 OK)
✅ Include step-by-step instructions
✅ Add deadlines and contacts
✅ Use plain English
✅ Cite legal basis
✅ Set appropriate reliability_score
✅ Keep information current

### Don't:
❌ Include medical/clinical advice
❌ Provide case-specific legal advice
❌ Use jargon without explanation
❌ Copy entire webpages (excerpt only)
❌ Duplicate entries
❌ Include opinion pieces

## Combining with Web Crawling

You can use BOTH approaches:

**Web Crawling** → General autism information  
**Structured Import** → Bureaucratic processes

Example workflow:
1. Crawl NHS, NAS for general autism info
2. Import structured guides for PIP, EHCP, Blue Badge
3. Result: Comprehensive knowledge base!

## Maintenance

**How often to update:**
- Government/council pages: Every 6 months
- Benefits/legal processes: Annually or when legislation changes
- NHS guidance: Annually
- Local services: Every 6 months

**What to check:**
- URLs still valid
- Deadlines unchanged
- Contact details current
- Legal references accurate

## Support

For questions or issues:
1. Check sample files in `data/`
2. Run with `--dry-run` to validate
3. Check import logs for errors
4. Verify ChromaDB collection

## Technical Details

- **Importer:** `rag/structured_importer.py`
- **Formatter:** `rag/structured_formatter.py`
- **Import Script:** `scripts/import_structured_knowledge.py`
- **Sample Data:** `data/sample_structured_knowledge.jsonl`
- **Storage:** ChromaDB with full metadata preservation
- **Embeddings:** SentenceTransformers (all-MiniLM-L6-v2)
