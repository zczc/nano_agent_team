import requests
import xml.etree.ElementTree as ET
from typing import Dict, Any, List
from backend.llm.decorators import schema_strict_validator

class ArxivSearchTool:
    """
    Search Arxiv for research papers.
    Uses the official arXiv API (http://export.arxiv.org/api/query).
    """

    @property
    def name(self) -> str:
        return "arxiv_search"

    @property
    def description(self) -> str:
        return "Search arXiv preprint repository for papers (physics, mathematics, computer science, quantitative biology, and related fields). Returns a list of papers with titles, authors, categories, summaries, and official URLs. Use this for literature reviews or validating technical concepts with peer-reviewed or preprint research."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query syntax (e.g. 'all:electron', 'ti:attention+AND+au:vaswani')."
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return (default 5).",
                    "default": 5
                }
            },
            "required": ["query"]
        }

    def to_openai_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema
            }
        }

    @schema_strict_validator
    def execute(self, query: str, max_results: int = 5) -> str:
        base_url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": query,
            "start": 0,
            "max_results": max_results
        }
        
        try:
            response = requests.get(base_url, params=params, timeout=10)
            if response.status_code != 200:
                return f"Error: arXiv API returned status code {response.status_code}"
            
            # Parse XML response
            root = ET.fromstring(response.content)
            
            # Namespace for arXiv API
            ns = {'atom': 'http://www.w3.org/2005/Atom', 'arxiv': 'http://arxiv.org/schemas/atom'}
            
            entries = root.findall('atom:entry', ns)
            if not entries:
                return "No results found."
            
            results = []
            for i, entry in enumerate(entries):
                title = entry.find('atom:title', ns).text.strip().replace('\n', ' ')
                summary = entry.find('atom:summary', ns).text.strip().replace('\n', ' ')
                published = entry.find('atom:published', ns).text
                
                authors = []
                for author in entry.findall('atom:author', ns):
                    name = author.find('atom:name', ns).text
                    authors.append(name)
                
                link = entry.find('atom:id', ns).text
                
                results.append(f"[{i+1}] **{title}**\n   Authors: {', '.join(authors)}\n   Published: {published}\n   Link: {link}\n   Summary: {summary[:300]}...\n")
            
            return "\n".join(results)
            
        except Exception as e:
            return f"Error querying arXiv API: {str(e)}"
