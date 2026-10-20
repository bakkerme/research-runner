"""Research Runner producer API; server dependencies are optional."""
__version__ = "0.3.0"


def __getattr__(name):
    if name == 'Client':
        from .client import Client
        return Client
    if name == 'ResearchRepo':
        from .repo import ResearchRepo
        return ResearchRepo
    raise AttributeError(name)
