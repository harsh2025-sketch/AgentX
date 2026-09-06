"""C6.06 orchestration entry point.

The implementation is deliberately delegated to the core contract so cognition
can construct requests without acquiring network data or mutating Hive state.
"""
from agentx.core.knowledge_revalidation import *
from agentx.core.knowledge_revalidation import KnowledgeRevalidator

__all__ = [name for name in globals() if not name.startswith("_")]
