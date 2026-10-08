
"""Source package for the AI moderator agent.

Deliberately empty of imports.

This module previously re-exported CommunityModeratorAgent,
create_moderator_session, ParticipantTracker and ModerationLogger. That made
`import src.anything` pull in moderator_agent, which imports livekit.plugins —
so 15 pure-domain test modules (turn_phase, text_analysis, constants, ...)
could not be collected without the full LiveKit stack installed.

No caller ever used the package-level names; every consumer imports the
submodule directly (agent.py, scripts/test_connection.py,
tests/test_connectivity.py). Keep it that way: add no imports here.
"""
