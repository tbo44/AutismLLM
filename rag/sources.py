"""
UK Autism Sources Configuration
Defines trusted sources for crawling autism information
"""

from dataclasses import dataclass
from typing import List, Dict
from enum import Enum

class SourceAuthority(Enum):
    GOVERNMENT = 1  # Highest authority
    NHS = 2
    NATIONAL_CHARITY = 3
    LOCAL_AUTHORITY = 4
    SPECIALIST_ORG = 5

@dataclass
class Source:
    name: str
    base_url: str
    authority: SourceAuthority
    crawl_paths: List[str]
    description: str
    location_specific: bool = False

# UK Autism Information Sources
UK_SOURCES = [
    # Government Sources (Highest Authority)
    Source(
        name="Gov.UK - SEND",
        base_url="https://www.gov.uk",
        authority=SourceAuthority.GOVERNMENT,
        crawl_paths=[
            "/children-with-special-educational-needs",
            "/children-with-special-educational-needs/extra-SEN-help",
            "/government/statistics/education-health-and-care-plans-england-2025",
            "/government/publications/send-19-to-25-year-olds-entitlement-to-ehc-plans/send-19-to-25-year-olds-entitlement-to-ehc-plans",
            "/disability-living-allowance-children"
        ],
        description="Official UK government guidance on SEND and benefits"
    ),
    
    # NHS Sources
    Source(
        name="NHS",
        base_url="https://www.nhs.uk",
        authority=SourceAuthority.NHS,
        crawl_paths=[
            "/conditions/autism/",
            "/conditions/autism/what-is-autism/",
            "/conditions/autism/signs/adults/",
            "/conditions/autism/signs/children/",
            "/conditions/autism/getting-diagnosed/how-to-get-diagnosed/",
            "/conditions/autism/getting-diagnosed/assessments/",
            "/conditions/autism/other-conditions/"
        ],
        description="NHS official autism information and guidance"
    ),
    
    # National Charities
    Source(
        name="National Autistic Society",
        base_url="https://www.autism.org.uk",
        authority=SourceAuthority.NATIONAL_CHARITY,
        crawl_paths=[
            "/advice-and-guidance",
            "/advice-and-guidance/topics/about-autism",
            "/advice-and-guidance/topics/diagnosis/before-diagnosis/signs-that-a-child-or-adult-may-be-autistic",
            "/advice-and-guidance/topics/mental-health",
            "/advice-and-guidance/topics/employment",
            "/advice-and-guidance/topics/strategies-and-interventions",
            "/advice-and-guidance/topics/resources-for-autistic-teenagers"
        ],
        description="UK's leading autism charity providing comprehensive support information"
    ),
    
    Source(
        name="Ambitious about Autism",
        base_url="https://www.ambitiousaboutautism.org.uk",
        authority=SourceAuthority.NATIONAL_CHARITY,
        crawl_paths=[
            "/about-us",
            "/about-us/media-centre/blog",
            "/contact-us"
        ],
        description="National charity focused on education and young people with autism"
    ),
    
    # Specialist Organizations
    Source(
        name="IPSEA",
        base_url="https://www.ipsea.org.uk",
        authority=SourceAuthority.SPECIALIST_ORG,
        crawl_paths=[
            "/appealing-to-the-send-tribunal",
            "/general-advice-for-all-appeals",
            "/how-to-submit-an-appeal-general-advice",
            "/pages/category/education-health-and-care-plans"
        ],
        description="Independent Provider of Special Education Advice"
    ),
    
    # Local Authority (Hounslow-specific)
    Source(
        name="Hounslow Council",
        base_url="https://www.hounslow.gov.uk",
        authority=SourceAuthority.LOCAL_AUTHORITY,
        crawl_paths=[
            "/info/20012/children_and_families/1047/special_educational_needs_and_disabilities_send",
            "/info/20012/children_and_families/1048/education_health_and_care_plans",
            "/info/20012/children_and_families/1049/local_offer"
        ],
        description="Hounslow local authority SEND services",
        location_specific=True
    ),
]

def get_sources_by_authority() -> Dict[SourceAuthority, List[Source]]:
    """Group sources by authority level for prioritized retrieval"""
    sources_by_authority = {}
    for source in UK_SOURCES:
        if source.authority not in sources_by_authority:
            sources_by_authority[source.authority] = []
        sources_by_authority[source.authority].append(source)
    return sources_by_authority

def get_hounslow_sources() -> List[Source]:
    """Get Hounslow-specific sources for local routing"""
    return [source for source in UK_SOURCES if source.location_specific]

def get_all_crawl_urls() -> List[tuple]:
    """Get all URLs to crawl with source metadata"""
    urls = []
    for source in UK_SOURCES:
        for path in source.crawl_paths:
            full_url = source.base_url + path
            urls.append((full_url, source.name, source.authority, source.description))
    return urls