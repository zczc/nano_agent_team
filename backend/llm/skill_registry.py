"""
LLM Skill Registry

Implements folder-based skill definition, loading, and dynamic injection mechanism.
Skills differ from subagents; they are "action guides" injected at task start to influence Agent behavior.

"""

import os
import yaml
from typing import List, Dict, Any, Optional
from backend.utils.logger import Logger

class Skill:
    """
    Skill Definition Class
    
    Represents a skill folder containing SKILL.md instruction file and optional resources.
    """
    def __init__(self, path: str):
        self.path = path
        self.name = ""
        self.description = ""
        self.instructions = ""
        self.allowed_tools: Optional[List[str]] = None
        self._load()

    def _load(self):
        """Load SKILL.md file"""
        skill_md_path = os.path.join(self.path, "SKILL.md")
        if not os.path.exists(skill_md_path):
            raise FileNotFoundError(f"SKILL.md not found in {self.path}")

        try:
            with open(skill_md_path, "r", encoding="utf-8") as f:
                content = f.read()

            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    meta = yaml.safe_load(parts[1]) or {}
                    self.name = meta.get("name", os.path.basename(self.path))
                    self.description = meta.get("description", "")
                    self.allowed_tools = meta.get("allowed-tools")
                    self.instructions = parts[2].strip()
        except Exception as e:
            Logger.error(f"Error loading skill at {self.path}: {e}")
            raise e

    def get_resource_path(self, resource_name: str) -> Optional[str]:
        """Get absolute path of resource in skill directory"""
        # Prevent path traversal
        resource_name = os.path.basename(resource_name)
        
        # Try common locations
        possible_paths = [
            os.path.join(self.path, resource_name),
            os.path.join(self.path, f"{resource_name}.md"),
            os.path.join(self.path, "templates", resource_name),
            os.path.join(self.path, "scripts", resource_name)
        ]
        
        for p in possible_paths:
            if os.path.exists(p):
                return p
        return None

class SkillRegistry:
    """
    Skill Registry
    
    Manages all skills in the system and provides matching logic.
    """
    def __init__(self, skills_dir: str):
        self.skills_dir = skills_dir
        self.skills: Dict[str, Skill] = {}
        self._load_skills()

    def _load_skills(self):
        """Scan skill directory and load all valid skills"""
        self.load_skills_from_dir(self.skills_dir)

    def load_skills_from_dir(self, skills_dir: str):
        """Load skills from specified directory"""
        if not os.path.exists(skills_dir):
            return

        for entry in os.scandir(skills_dir):
            if entry.is_dir():
                try:
                    skill = Skill(entry.path)
                    self.skills[skill.name] = skill
                except Exception:
                    continue

    def find_best_skill(self, query: str) -> Optional[Skill]:
        """
        Match best skill based on query content
        
        Current implementation: Keyword matching based on description (simple heuristic).
        Future expansion: Semantic matching using LLM.
        """
        query_lower = query.lower()
        best_match = None
        max_score = 0

        for skill in self.skills.values():
            # Simple keyword weighting
            score = 0
            desc_words = skill.description.lower().replace(",", " ").replace(".", " ").split()
            for word in desc_words:
                if len(word) > 2 and word in query_lower:
                    score += 1
            
            if score > max_score:
                max_score = score
                best_match = skill
        
        # Return only if score exceeds threshold (avoid false positives)
        return best_match if max_score >= 1 else None

    def get_skill(self, name: str) -> Optional[Skill]:
        """Get skill by name"""
        return self.skills.get(name)

    def get_skills_metadata(self) -> List[Dict[str, str]]:
        """Get metadata list (name and description) of all skills for LLM matching"""
        return [
            {"name": skill.name, "description": skill.description}
            for skill in self.skills.values()
        ]
