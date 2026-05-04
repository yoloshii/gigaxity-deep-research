"""
Passage Extraction Module

Extracts relevant passages from documents instead of using full content.
This addresses Gap #3: Full Document Retrieval.

Key insight from "Lost in the Middle" paper (Liu et al.):
LLMs struggle to use information in the middle of long contexts.
Extracting relevant passages dramatically improves synthesis quality.
"""

import re
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class Passage:
    """A relevant passage extracted from a source."""
    text: str
    source_id: str
    source_url: str
    source_title: str
    relevance_score: float
    start_char: int
    end_char: int
    context_before: str = ""
    context_after: str = ""


class PassageExtractor:
    """
    Extracts relevant passages from source documents.

    Uses semantic similarity to find the most relevant chunks
    for a given query.
    """

    def __init__(
        self,
        chunk_size: int = 500,
        chunk_overlap: int = 100,
        embedding_model: Optional[str] = None,
    ):
        """
        Initialize the passage extractor.

        Args:
            chunk_size: Target size of each chunk in characters
            chunk_overlap: Overlap between chunks
            embedding_model: Optional model name for semantic matching
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.embedding_model = embedding_model
        self._embedder = None

    @property
    def embedder(self):
        """Lazy load embedder to avoid import if not needed."""
        if self._embedder is None and self.embedding_model:
            try:
                from sentence_transformers import SentenceTransformer
                self._embedder = SentenceTransformer(self.embedding_model)
            except ImportError:
                pass
        return self._embedder

    def extract_passages(
        self,
        query: str,
        content: str,
        source_id: str,
        source_url: str,
        source_title: str,
        top_k: int = 3,
    ) -> list[Passage]:
        """
        Extract the most relevant passages from content.

        Args:
            query: The search query
            content: Full document content
            source_id: Source identifier
            source_url: Source URL
            source_title: Source title
            top_k: Number of passages to extract

        Returns:
            List of Passage objects sorted by relevance
        """
        if not content or not content.strip():
            return []

        # Chunk the content
        chunks = self._chunk_content(content)

        if not chunks:
            return []

        # Score chunks
        if self.embedder:
            scored_chunks = self._score_semantic(query, chunks)
        else:
            scored_chunks = self._score_keyword(query, chunks)

        # Select top-k non-overlapping chunks
        selected = self._select_diverse(scored_chunks, top_k)

        # Convert to passages
        passages = []
        for chunk, score in selected:
            passages.append(Passage(
                text=chunk['text'],
                source_id=source_id,
                source_url=source_url,
                source_title=source_title,
                relevance_score=score,
                start_char=chunk['start'],
                end_char=chunk['end'],
                context_before=chunk.get('context_before', ''),
                context_after=chunk.get('context_after', ''),
            ))

        return passages

    def _chunk_content(self, content: str) -> list[dict]:
        """
        Split content into overlapping chunks.

        Uses sentence boundaries when possible for coherent chunks.
        """
        # Clean content
        content = self._clean_content(content)

        if len(content) <= self.chunk_size:
            return [{
                'text': content,
                'start': 0,
                'end': len(content),
            }]

        chunks = []
        sentences = self._split_sentences(content)

        current_chunk = []
        current_length = 0
        chunk_start = 0

        for sentence in sentences:
            sentence_len = len(sentence)

            if current_length + sentence_len > self.chunk_size and current_chunk:
                # Finalize current chunk
                chunk_text = ' '.join(current_chunk)
                chunks.append({
                    'text': chunk_text,
                    'start': chunk_start,
                    'end': chunk_start + len(chunk_text),
                })

                # Overlap: keep last few sentences
                overlap_text = ''
                overlap_sentences = []
                for s in reversed(current_chunk):
                    if len(overlap_text) + len(s) > self.chunk_overlap:
                        break
                    overlap_sentences.insert(0, s)
                    overlap_text = ' '.join(overlap_sentences)

                current_chunk = overlap_sentences
                current_length = len(overlap_text)
                chunk_start = chunk_start + len(chunk_text) - len(overlap_text)

            current_chunk.append(sentence)
            current_length += sentence_len + 1  # +1 for space

        # Final chunk
        if current_chunk:
            chunk_text = ' '.join(current_chunk)
            chunks.append({
                'text': chunk_text,
                'start': chunk_start,
                'end': chunk_start + len(chunk_text),
            })

        return chunks

    def _clean_content(self, content: str) -> str:
        """Clean content for chunking."""
        # Remove excessive whitespace
        content = re.sub(r'\s+', ' ', content)
        # Remove very long non-word sequences (likely code/data)
        content = re.sub(r'[^\w\s]{50,}', ' ', content)
        return content.strip()

    def _split_sentences(self, text: str) -> list[str]:
        """Split text into sentences."""
        # Simple sentence splitter
        # Could be improved with NLTK or spaCy
        sentences = re.split(r'(?<=[.!?])\s+', text)
        return [s.strip() for s in sentences if s.strip()]

    def _score_semantic(self, query: str, chunks: list[dict]) -> list[tuple[dict, float]]:
        """Score chunks using semantic similarity."""
        query_embedding = self.embedder.encode(query, convert_to_numpy=True)
        chunk_texts = [c['text'] for c in chunks]
        chunk_embeddings = self.embedder.encode(chunk_texts, convert_to_numpy=True)

        # Cosine similarity
        similarities = np.dot(chunk_embeddings, query_embedding) / (
            np.linalg.norm(chunk_embeddings, axis=1) * np.linalg.norm(query_embedding)
        )

        return [(chunk, float(sim)) for chunk, sim in zip(chunks, similarities)]

    def _score_keyword(self, query: str, chunks: list[dict]) -> list[tuple[dict, float]]:
        """Score chunks using keyword matching (fallback)."""
        # Tokenize query
        query_terms = set(re.findall(r'\w+', query.lower()))

        scored = []
        for chunk in chunks:
            chunk_terms = set(re.findall(r'\w+', chunk['text'].lower()))

            # Jaccard-like overlap
            if not query_terms:
                score = 0.0
            else:
                overlap = len(query_terms & chunk_terms)
                score = overlap / len(query_terms)

            # Boost for exact phrase match
            if query.lower() in chunk['text'].lower():
                score += 0.3

            scored.append((chunk, min(score, 1.0)))

        return scored

    def _select_diverse(
        self,
        scored_chunks: list[tuple[dict, float]],
        top_k: int
    ) -> list[tuple[dict, float]]:
        """
        Select top-k chunks with diversity.

        Avoids selecting overlapping chunks.
        """
        # Sort by score
        sorted_chunks = sorted(scored_chunks, key=lambda x: x[1], reverse=True)

        selected = []
        selected_ranges = []

        for chunk, score in sorted_chunks:
            if len(selected) >= top_k:
                break

            # Check overlap with already selected
            start, end = chunk['start'], chunk['end']
            overlaps = False

            for sel_start, sel_end in selected_ranges:
                # Check if ranges overlap
                if start < sel_end and end > sel_start:
                    overlaps = True
                    break

            if not overlaps:
                selected.append((chunk, score))
                selected_ranges.append((start, end))

        return selected
