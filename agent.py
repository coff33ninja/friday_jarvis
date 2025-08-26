from dotenv import load_dotenv

from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions
from livekit.plugins import (
    noise_cancellation,
)
from livekit.plugins import google
from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION
from tools import get_weather, search_web, send_email, read_emails
load_dotenv()

# Context sizing constants
MAX_CONTEXT_SIZE = 25600  # max allowed (tokens or chars)
TARGET_CONTEXT_SIZE = 12800  # preferred size

def trim_context(context: str, max_size: int = MAX_CONTEXT_SIZE, target_size: int = TARGET_CONTEXT_SIZE) -> str:
    """
    Trims context to target size if it exceeds max_size. Uses character count as proxy for tokens.
    """
    if len(context) > max_size:
        # Hard trim to max_size
        return context[:target_size]
    elif len(context) > target_size:
        # Soft trim to target_size
        return context[:target_size]
    return context


class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=AGENT_INSTRUCTION,
            llm=google.beta.realtime.RealtimeModel(
            voice="Leda",
            temperature=0.8,
        ),
            tools=[
                get_weather,
                search_web,
                send_email,
                read_emails
            ],
        )
        


async def entrypoint(ctx: agents.JobContext):
    session = AgentSession(
        
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_input_options=RoomInputOptions(
            # LiveKit Cloud enhanced noise cancellation
            # - If self-hosting, omit this parameter
            # - For telephony applications, use `BVCTelephony` for best results
            video_enabled=True,
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await ctx.connect()

    # Enforce context sizing before sending to LLM
    trimmed_instructions = trim_context(SESSION_INSTRUCTION)
    await session.generate_reply(
        instructions=trimmed_instructions,
    )


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))