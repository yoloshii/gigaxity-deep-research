"""Pytest configuration and fixtures."""

import os
import pytest
from pathlib import Path
from dotenv import load_dotenv

# Load test environment
test_env = Path(__file__).parent / ".env"
if test_env.exists():
    load_dotenv(test_env)


def pytest_configure(config):
    """Configure custom markers."""
    config.addinivalue_line("markers", "unit: Unit tests (no external dependencies)")
    config.addinivalue_line("markers", "integration: Integration tests (require external services)")
    config.addinivalue_line("markers", "slow: Slow tests (LLM synthesis)")
    config.addinivalue_line("markers", "benchmark: Performance benchmark tests")
    config.addinivalue_line("markers", "p0: P0 enhancement tests")
    config.addinivalue_line("markers", "p1: P1 enhancement tests")


@pytest.fixture
def sample_query():
    """Sample search query for tests."""
    return "python async programming tutorial"


@pytest.fixture
def comparison_query():
    """Query for comparison/synthesis tests."""
    return "Compare FastAPI vs Flask for production APIs"


@pytest.fixture
def tutorial_query():
    """Query for tutorial focus mode tests."""
    return "How to implement OAuth2 authentication in FastAPI"


@pytest.fixture
def academic_query():
    """Query for academic focus mode tests."""
    return "transformer attention mechanisms research papers"


@pytest.fixture
def debugging_query():
    """Query for debugging focus mode tests."""
    return "Python asyncio RuntimeError event loop already running"


@pytest.fixture
def sample_sources():
    """Sample source data for testing."""
    from src.connectors.base import Source
    return [
        Source(
            id="sx_test001",
            title="Python Async IO Guide",
            url="https://example.com/async-guide",
            content="Learn how to use async/await in Python for concurrent programming.",
            score=0.95,
            connector="searxng",
        ),
        Source(
            id="tv_test002",
            title="Async Programming Best Practices",
            url="https://example.com/async-best-practices",
            content="Best practices for writing async code in Python applications.",
            score=0.88,
            connector="tavily",
        ),
        Source(
            id="lu_test003",
            title="Python Concurrency Deep Dive",
            url="https://example.com/concurrency",
            content="Deep dive into Python's concurrency model and asyncio library.",
            score=0.82,
            connector="linkup",
        ),
    ]


@pytest.fixture
def pre_gathered_sources():
    """Pre-gathered sources for synthesis tests."""
    from src.synthesis import PreGatheredSource
    return [
        PreGatheredSource(
            origin="ref",
            url="https://fastapi.tiangolo.com/",
            title="FastAPI Documentation",
            content="""FastAPI is a modern, fast (high-performance), web framework for building APIs
            with Python 3.7+ based on standard Python type hints. Key features include:
            - Fast: Very high performance, on par with NodeJS and Go
            - Fast to code: Increase the speed to develop features by about 200% to 300%
            - Fewer bugs: Reduce about 40% of human (developer) induced errors
            - Intuitive: Great editor support. Completion everywhere. Less time debugging.
            - Easy: Designed to be easy to use and learn. Less time reading docs.
            - Short: Minimize code duplication. Multiple features from each parameter declaration.
            - Robust: Get production-ready code. With automatic interactive documentation.
            - Standards-based: Based on (and fully compatible with) OpenAPI and JSON Schema.""",
            source_type="documentation",
            metadata={"version": "0.100.0"},
        ),
        PreGatheredSource(
            origin="exa",
            url="https://flask.palletsprojects.com/",
            title="Flask Documentation",
            content="""Flask is a lightweight WSGI web application framework. It is designed to make
            getting started quick and easy, with the ability to scale up to complex applications.
            It began as a simple wrapper around Werkzeug and Jinja and has become one of the most
            popular Python web application frameworks. Flask offers suggestions but doesn't enforce
            any dependencies or project layout. It is up to the developer to choose the tools and
            libraries they want to use. There are many extensions provided by the community that
            make adding new functionality easy. Flask is considered a micro-framework because it
            doesn't require particular tools or libraries.""",
            source_type="documentation",
            metadata={"version": "3.0.0"},
        ),
        PreGatheredSource(
            origin="jina",
            url="https://blog.example.com/fastapi-vs-flask-benchmarks",
            title="FastAPI vs Flask Performance Benchmarks 2024",
            content="""In our comprehensive benchmark tests comparing FastAPI and Flask:
            - FastAPI handles 3x more requests per second than Flask
            - FastAPI's async support provides better concurrency handling
            - Flask has a larger ecosystem of extensions
            - FastAPI has automatic OpenAPI documentation
            - Flask is simpler for small applications
            - FastAPI requires understanding of async/await patterns
            - Both frameworks are production-ready
            - FastAPI is recommended for new API projects requiring high performance""",
            source_type="article",
            metadata={"date": "2024-01"},
        ),
    ]


@pytest.fixture
def contradicting_sources():
    """Sources with contradicting information for testing."""
    from src.synthesis import PreGatheredSource
    return [
        PreGatheredSource(
            origin="source_a",
            url="https://example.com/view-a",
            title="React State Management Guide",
            content="""Redux is essential for managing state in React applications.
            Without Redux, complex applications become unmaintainable. Redux provides
            a single source of truth and makes debugging easier. All serious React
            projects should use Redux from the start.""",
            source_type="article",
        ),
        PreGatheredSource(
            origin="source_b",
            url="https://example.com/view-b",
            title="Modern React Patterns",
            content="""Redux is often unnecessary in modern React. React Context and
            useReducer provide sufficient state management for most applications.
            Redux adds complexity and boilerplate that isn't needed. Many teams are
            moving away from Redux to simpler solutions like Zustand or Jotai.""",
            source_type="article",
        ),
    ]


@pytest.fixture
def low_quality_sources():
    """Low quality sources for quality gate testing."""
    from src.synthesis import PreGatheredSource
    return [
        PreGatheredSource(
            origin="unknown",
            url="https://spam-site.example.com/article",
            title="Click Here for Amazing Python Tips!!!",
            content="Buy our course! Only $99! Limited time offer! Python is great! Subscribe now!",
            source_type="article",
        ),
        PreGatheredSource(
            origin="unknown",
            url="https://outdated.example.com/python2",
            title="Python 2.7 Tutorial",
            content="print 'Hello World' - This tutorial covers Python 2.7 basics from 2010.",
            source_type="article",
            metadata={"date": "2010-01-01"},
        ),
    ]


@pytest.fixture
def searxng_configured():
    """Check if SearXNG is configured."""
    host = os.getenv("RESEARCH_SEARXNG_HOST", "")
    return bool(host)


@pytest.fixture
def tavily_configured():
    """Check if Tavily is configured."""
    key = os.getenv("RESEARCH_TAVILY_API_KEY", "")
    return bool(key)


@pytest.fixture
def linkup_configured():
    """Check if LinkUp is configured."""
    key = os.getenv("RESEARCH_LINKUP_API_KEY", "")
    return bool(key)


@pytest.fixture
def llm_configured():
    """Check if a real LLM is configured.

    Live-LLM tests gate on this fixture so they skip when no key is set; the
    base URL is not sufficient because it has a default of
    https://openrouter.ai/api/v1, which would otherwise make this fixture
    always-true and run live tests against an unauthenticated endpoint.
    """
    return bool(os.getenv("RESEARCH_LLM_API_KEY", ""))


@pytest.fixture
def llm_client():
    """Create LLM client for tests."""
    from openai import AsyncOpenAI
    from src.config import settings

    if not settings.llm_api_base:
        pytest.skip("LLM not configured")

    return AsyncOpenAI(
        base_url=settings.llm_api_base,
        api_key=settings.llm_api_key,
    )


@pytest.fixture
def search_aggregator():
    """Create search aggregator for tests."""
    from src.search import SearchAggregator
    return SearchAggregator()


@pytest.fixture
def test_client():
    """Create FastAPI test client."""
    from fastapi.testclient import TestClient
    from src.main import app
    return TestClient(app)


# Async fixtures for async tests
@pytest.fixture
def anyio_backend():
    """Backend for anyio async tests."""
    return "asyncio"
