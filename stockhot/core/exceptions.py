"""Exception classes for StockHot-CN."""


class StockHotError(Exception):
    """Base exception for all StockHot-CN errors."""
    pass


class DataSourceError(StockHotError):
    """Raised when data source is unavailable or returns error."""
    pass


class DataParseError(StockHotError):
    """Raised when data parsing fails."""
    pass


class AIAnalyzerError(StockHotError):
    """Raised when AI analysis fails."""
    pass


class ImageGenerationError(StockHotError):
    """Raised when image generation fails."""
    pass


class PublishError(StockHotError):
    """Raised when publishing to social media fails."""
    pass


class DatabaseError(StockHotError):
    """Raised when database operation fails."""
    pass