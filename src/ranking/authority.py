"""
Authority Scoring Module

Scores sources based on domain reputation and trustworthiness.
This is a critical component for ranking quality.
"""

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass
class AuthorityScore:
    """Authority score with breakdown."""
    total: float
    domain_trust: float
    source_type: float
    tld_trust: float


class AuthorityScorer:
    """
    Scores source authority based on domain reputation.

    Uses a combination of:
    1. Domain trust scores (curated list)
    2. Source type classification
    3. TLD trust levels
    """

    # High-authority domains by category
    TRUSTED_DOMAINS = {
        # Academic/Research
        'arxiv.org': 0.95,
        'scholar.google.com': 0.90,
        'semanticscholar.org': 0.90,
        'pubmed.ncbi.nlm.nih.gov': 0.95,
        'nature.com': 0.95,
        'science.org': 0.95,
        'ieee.org': 0.90,
        'acm.org': 0.90,
        'springer.com': 0.85,

        # Technical Documentation
        'docs.python.org': 0.95,
        'developer.mozilla.org': 0.95,
        'docs.microsoft.com': 0.90,
        'cloud.google.com': 0.90,
        'docs.aws.amazon.com': 0.90,
        'kubernetes.io': 0.90,
        'pytorch.org': 0.90,
        'tensorflow.org': 0.90,
        'huggingface.co': 0.85,

        # Code/Development
        'github.com': 0.80,
        'stackoverflow.com': 0.75,
        'gitlab.com': 0.75,

        # Quality Media
        'wikipedia.org': 0.80,
        'britannica.com': 0.85,
        'reuters.com': 0.85,
        'apnews.com': 0.85,
        'bbc.com': 0.80,
        'nytimes.com': 0.80,

        # Tech News (moderate trust - verify claims)
        'techcrunch.com': 0.65,
        'theverge.com': 0.65,
        'arstechnica.com': 0.70,
        'wired.com': 0.65,
    }

    # Domains to penalize (low quality, SEO spam, etc.)
    LOW_TRUST_DOMAINS = {
        'medium.com': 0.40,  # Variable quality
        'quora.com': 0.35,
        'reddit.com': 0.35,  # Need specific subreddit analysis
        'pinterest.com': 0.20,
        'twitter.com': 0.30,
        'facebook.com': 0.25,
    }

    # TLD trust levels
    TLD_TRUST = {
        '.edu': 0.90,
        '.gov': 0.90,
        '.org': 0.70,
        '.io': 0.60,
        '.com': 0.50,
        '.net': 0.50,
        '.co': 0.45,
        '.info': 0.35,
        '.xyz': 0.25,
        '.biz': 0.30,
    }

    # Source type patterns
    DOC_PATTERNS = [
        (r'/docs?/', 0.85),
        (r'/documentation/', 0.85),
        (r'/reference/', 0.80),
        (r'/api/', 0.80),
        (r'/guide/', 0.75),
        (r'/tutorial/', 0.70),
        (r'/blog/', 0.50),
        (r'/news/', 0.55),
    ]

    def __init__(self):
        """Initialize the authority scorer."""
        self._compile_patterns()

    def _compile_patterns(self):
        """Compile regex patterns."""
        self.doc_patterns = [(re.compile(p, re.I), score) for p, score in self.DOC_PATTERNS]

    def score(self, url: str) -> AuthorityScore:
        """
        Score the authority of a URL.

        Args:
            url: The source URL

        Returns:
            AuthorityScore with breakdown
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            path = parsed.path.lower()

            # Remove www. prefix
            if domain.startswith('www.'):
                domain = domain[4:]

            # Get domain trust
            domain_trust = self._get_domain_trust(domain)

            # Get TLD trust
            tld_trust = self._get_tld_trust(domain)

            # Get source type score
            source_type = self._get_source_type_score(path)

            # Weighted combination
            total = (
                0.50 * domain_trust +
                0.30 * source_type +
                0.20 * tld_trust
            )

            return AuthorityScore(
                total=total,
                domain_trust=domain_trust,
                source_type=source_type,
                tld_trust=tld_trust
            )

        except Exception:
            # Return neutral score on parse errors
            return AuthorityScore(
                total=0.5,
                domain_trust=0.5,
                source_type=0.5,
                tld_trust=0.5
            )

    def _get_domain_trust(self, domain: str) -> float:
        """Get trust score for domain."""
        # Check exact match
        if domain in self.TRUSTED_DOMAINS:
            return self.TRUSTED_DOMAINS[domain]
        if domain in self.LOW_TRUST_DOMAINS:
            return self.LOW_TRUST_DOMAINS[domain]

        # Check subdomain matches (e.g., blog.example.com matches example.com)
        parts = domain.split('.')
        for i in range(len(parts)):
            parent = '.'.join(parts[i:])
            if parent in self.TRUSTED_DOMAINS:
                # Slight penalty for subdomains
                return self.TRUSTED_DOMAINS[parent] * 0.95
            if parent in self.LOW_TRUST_DOMAINS:
                return self.LOW_TRUST_DOMAINS[parent]

        # Default neutral
        return 0.50

    def _get_tld_trust(self, domain: str) -> float:
        """Get trust score based on TLD."""
        for tld, score in self.TLD_TRUST.items():
            if domain.endswith(tld):
                return score
        return 0.40  # Unknown TLD

    def _get_source_type_score(self, path: str) -> float:
        """Score based on URL path patterns."""
        for pattern, score in self.doc_patterns:
            if pattern.search(path):
                return score
        return 0.50  # Default
