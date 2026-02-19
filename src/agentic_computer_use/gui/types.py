"""Data types for GUI agent operations."""
from dataclasses import dataclass


@dataclass
class GroundingResult:
    """Result of locating a UI element on screen."""
    x: int
    y: int
    confidence: float = 0.0
    description: str = ""
    element_text: str = ""
