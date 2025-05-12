import asyncio
import json
from typing import Optional, List, Dict
from enum import Enum

from agents import Agent, Runner, RunContextWrapper, function_tool
from config import SYSTEM_PROMPT # For agent initialization
from pydantic import BaseModel, Field

class QuestState(Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"

class Objective(BaseModel):
    objective: str = Field(description="Description of the objective to be completed.")
    finished: bool = Field(description="Whether this objective has been completed or not.")
    partially_complete: Optional[str] = Field(default=None, description="If partially complete, a string explaining what's done and what's missing.")

class Character(BaseModel):
    name: str = Field(description="Character name (lowercase).")
    description: str = Field(description="Detailed description of the character's appearance and personality.")
    in_scene: bool = Field(description="Whether the character is currently in the scene.")

class GameContext(BaseModel):
    """Tracks the current state of the game."""
    quest_state: QuestState = Field(default=QuestState.NOT_STARTED, description="Current state of the quest.")
    objectives: List[Objective] = Field(default_factory=list, description="List of objectives for the current quest.")
    characters: List[Character] = Field(default_factory=list, description="List of all characters in the game.")
    current_turn: int = Field(default=0, description="Current turn number.")
    theme: Optional[str] = Field(default=None, description="Selected theme for the game.")
    environment: Optional[str] = Field(default=None, description="Current environment/area of the game.")
    entities: List[str] = Field(default_factory=list, description="List of interactive entities in the current environment.")

    def get_characters_in_scene(self) -> List[str]:
        """Returns list of character names currently in the scene."""
        return [char.name for char in self.characters if char.in_scene]

    def update_character_scene_status(self, character_names: List[str]):
        """Updates which characters are in the current scene."""
        for char in self.characters:
            char.in_scene = char.name in character_names

    def check_all_objectives_completed(self) -> bool:
        """Checks if all objectives are completed."""
        if not self.objectives: # No objectives means nothing to complete
            return False
        return all(obj.finished for obj in self.objectives)

    def update_objective_status(self, objective_index: int, finished: bool):
        """Updates the status of a specific objective."""
        if 0 <= objective_index < len(self.objectives):
            self.objectives[objective_index].finished = finished

# Pydantic Model for the expected story response structure
class StoryResponse(BaseModel):
    image_prompt: str = Field(description="Detailed description of the scene with names and objects with detailed characteristics.")
    characters_in_scene: List[str] = Field(description="List of character names present in the scene (lowercase).")
    narration: str = Field(description="Vivid scene description with names and characteristics.")
    choices: List[str] = Field(description="2-4 short unique actionable choice options.")
    # objectives: List[Objective] = Field(description="List of objectives for the current quest, with their completion status.") # Removed: Agent will use a tool

    # Optional: Add a model_config for Pydantic v2 if needed, or examples directly in Field
    # class Config:
    #     schema_extra = {
    #         "example": {
    #             "image_prompt": "A detailed description...",
    #             "characters_in_scene": ["aurora", "davi"],
    #             "narration": "Aurora and Davi are in the enchanted garden...",
    #             "choices": ["Touch the glowing flower", "Call out to Davi"],
    #             "objectives": [
    #                 {"objective": "Touch the glowing flower", "finished": False},
    #                 {"objective": "Call out to Davi", "finished": False}
    #             ]
    #         }
    #     }

@function_tool
async def update_game_objectives_tool(
    ctx: RunContextWrapper[GameContext], 
    objectives: List[Objective]
) -> str:
    """
    Call this tool to set or update the player's game objectives.
    Provide a list of all current objectives, including their 'objective' description string,
    'finished' status (boolean), and optionally a 'partially_complete' string
    if an objective is in progress but not fully completed.
    """
    log_prefix = "[Agent Tool: update_game_objectives_tool]"
    game_context = ctx.context # Access GameContext via RunContextWrapper

    if game_context is None:
        print(f"{log_prefix} Error: Game context not available via RunContextWrapper.")
        return "Error: Game context not available. Cannot update objectives."

    print(f"{log_prefix} Received objectives: {objectives}")
    game_context.objectives = objectives

    if not objectives:
        game_context.quest_state = QuestState.NOT_STARTED
        print(f"{log_prefix} No objectives. Quest state: {game_context.quest_state}.")
    elif game_context.check_all_objectives_completed():
        game_context.quest_state = QuestState.COMPLETED
        print(f"{log_prefix} All objectives completed. Quest state: {game_context.quest_state}.")
    elif game_context.quest_state == QuestState.NOT_STARTED and any(objectives):
        game_context.quest_state = QuestState.IN_PROGRESS
        print(f"{log_prefix} Objectives present, quest was NOT_STARTED. Quest state: {game_context.quest_state}.")
    elif game_context.quest_state == QuestState.COMPLETED and not game_context.check_all_objectives_completed():
        game_context.quest_state = QuestState.IN_PROGRESS # Re-opened
        print(f"{log_prefix} Quest was COMPLETED, new/unfinished objectives. Quest state: {game_context.quest_state}.")

    print(f"{log_prefix} Game context objectives updated. Current quest state: {game_context.quest_state}")
    return "Objectives updated successfully in the game state."

def initialize_storyteller_agent() -> Agent:
    """Initializes and returns the storyteller agent with structured output."""
    storyteller_agent = Agent(
        name="Storyteller Agent Aurora 3",
        instructions=SYSTEM_PROMPT,
        model="gpt-4.1",
        output_type=StoryResponse,
        tools=[update_game_objectives_tool]
    )
    print("[Agent Service] Storyteller Agent initialized with StoryResponse output type and update_game_objectives_tool.")
    return storyteller_agent

async def get_agent_story_response(runner: Runner, game_context: GameContext, current_turn_user_input: str, conversation_history: List[Dict[str, str]], session_id: str) -> Optional[StoryResponse]:
    """
    Gets a structured story response from the agent.
    The Agent SDK is expected to manage history internally based on the agent instance.
    The conversation_history parameter is kept for now for logging/debugging but NOT directly passed to Runner.run if it only accepts 'input'.
    """
    log_prefix = f"[Agent Service][Session {session_id}]"
    safe_user_input_snippet = str(current_turn_user_input[:50]).replace('"', '\"').replace("'", "\'")
    print(f"{log_prefix} Getting structured response. Input: '{safe_user_input_snippet}...'. History len: {len(conversation_history)}.")

    # Ensure the runner has the most up-to-date game_context set on its instance.
    # This is often used by the SDK to make context available to tools via RunContextWrapper.
    runner.context = game_context 

    try:
        # Attempt to pass context directly to the run method as well, if supported by the SDK.
        # This can be more robust for tool context in some SDK versions.
        result = await Runner.run(
            runner.agent, 
            input=current_turn_user_input, 
            context=game_context # Explicitly pass context here
        )
        
        if result and result.final_output:
            if isinstance(result.final_output, StoryResponse):
                game_context.update_character_scene_status(result.final_output.characters_in_scene)
                print(f"{log_prefix} Agent SDK Response (StoryResponse model). Narration snippet: ...")
                print(f"{log_prefix} Post-tool call context: Objectives count = {len(game_context.objectives)}, Quest State = {game_context.quest_state}")
                return result.final_output
            else:
                safe_output_str = str(result.final_output)[:200].replace("\n", " ").replace("\r", " ").replace('"', '\"').replace("'", "\'")
                error_log_message = f"Agent SDK returned final_output but not StoryResponse. Type: {type(result.final_output)}. Output: {safe_output_str}"
                print(f"{log_prefix} {error_log_message}")
                if isinstance(result.final_output, str):
                    try:
                        data = json.loads(result.final_output)
                        if "objectives" in data:
                            print(f"{log_prefix} WARNING: Agent included 'objectives' in JSON. Removing.")
                            del data["objectives"]
                        return StoryResponse(**data)
                    except Exception as parse_e:
                        print(f"{log_prefix} Fallback JSON parsing failed for string output: {parse_e}")
                return None
        else:
            safe_result_str = str(result).replace("\n", " ").replace("\r", " ").replace('"', '\"').replace("'", "\'")
            no_output_log_message = f"Agent SDK returned None result or no final_output. Result: {safe_result_str}"
            print(f"{log_prefix} {no_output_log_message}")
            return None
    except Exception as e:
        print(f"{log_prefix} !!! Agent SDK Call Error: {e}")
        return None 