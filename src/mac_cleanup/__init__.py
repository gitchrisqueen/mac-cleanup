"""mac-cleanup: a macOS disk-space auditor that can delete."""

__version__ = "0.1.0.dev0"

# Bump on any change to sizing semantics. Cache entries written by a different engine
# version are treated as absent rather than migrated.
ENGINE_V = 1
