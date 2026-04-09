"""Dream mode: post-session memory consolidation.

Flow:
  Stop hook → transcript.load → gate.decide → (if worth) agent.run →
  router.dispatch → reindex → digest.write
"""
