import logging
import threading
import time
from typing import Any, AsyncIterable, Dict, Tuple
from google.adk.agents.llm_agent import LlmAgent
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from google.adk.tools.mcp_tool.mcp_toolset import StdioServerParameters
from google.adk.models.lite_llm import LiteLlm
import json
import asyncio
from utils import SchemaFixOptions, create_patched_toolset

from dotenv import load_dotenv
load_dotenv()

# Configuration constants
MCP_SERVER_PATH = "/Users/saber/Library/Mobile Documents/com~apple~CloudDocs/Documents/GitHub/mcp-ethers-server/build/src/mcpServer.js"
MODEL_GPT_4O = "openai/gpt-4o"
DEFAULT_TIMEOUT = 30  # seconds
INIT_TIMEOUT = 10  # seconds for initialization waits

# Initialize commands to run at startup
INIT_COMMANDS = [
    "Please load this private key to the `Local` blockchain: 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "What is my wallet address?",  # This will verify if the wallet was loaded successfully
    "What is the current balance of my wallet on the Local blockchain for the address derived from the private key?",
    # Add more initialization commands here
]

# Global state for MCP connection
_mcp_tools = []
_mcp_event_loop = None
_mcp_thread = None
_exit_stack = None
_mcp_ready = False

def _start_mcp_background_thread():
    """Initialize and start MCP server in a background thread"""
    global _mcp_thread, _mcp_tools, _mcp_event_loop, _exit_stack, _mcp_ready
    
    def run_mcp_server():
        """Function to run in the background thread"""
        global _mcp_event_loop, _mcp_tools, _exit_stack, _mcp_ready
        
        print("Starting MCP server connection in background thread...")
        _mcp_event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_mcp_event_loop)
        
        async def initialize_mcp():
            """Initialize the MCP connection"""
            global _mcp_tools, _exit_stack, _mcp_ready
            
            try:
                options = SchemaFixOptions(
                    convert_type_arrays_to='string',
                    validate_api_key=True,
                    verbose=False,
                    aggressive=True
                )
                
                tools, exit_stack = await create_patched_toolset(
                    connection_params=StdioServerParameters(
                        command='node',
                        args=[MCP_SERVER_PATH],
                    ),
                    options=options,
                    logger_level=logging.ERROR
                )
                
                _mcp_tools = tools
                _exit_stack = exit_stack
                _mcp_ready = True
                
                print(f"Connected to MCP server - {len(tools)} tools available")
                
                # Keep the event loop running
                while True:
                    await asyncio.sleep(1)
                    
            except Exception as e:
                print(f"Error in MCP connection: {e}")
                _mcp_ready = False
            
        _mcp_event_loop.run_until_complete(initialize_mcp())
        
    # Start the thread and wait for initialization
    _mcp_thread = threading.Thread(target=run_mcp_server, daemon=True)
    _mcp_thread.start()
    
    # Wait for initialization with timeout
    start_time = time.time()
    while not _mcp_ready and time.time() - start_time < DEFAULT_TIMEOUT:
        time.sleep(0.1)
    
    if not _mcp_ready:
        print("Timed out waiting for MCP server to initialize")

# Start the MCP server when module is imported
_start_mcp_background_thread()

# Custom JSON serializer to handle special types
def custom_serializer(obj):
    """Handle non-serializable types for JSON conversion"""
    if hasattr(obj, '__dict__'):
        return obj.__dict__
    elif hasattr(obj, 'to_dict'):
        return obj.to_dict()
    else:
        return str(obj)

class Web3Agent:
    """Web3 Agent for blockchain interactions"""

    SUPPORTED_CONTENT_TYPES = ["text", "text/plain"]

    def __init__(self):
        self._agent = None
        self._user_id = "remote_agent"
        self._runner = None
        self._tools = []
        self._init_completed = False
        
    async def initialize(self):
        """Initialize the agent using the shared MCP tools"""
        global _mcp_tools
        
        # Wait for tools to be available
        start = time.time()
        while not _mcp_tools and time.time() - start < INIT_TIMEOUT:
            print("Waiting for MCP tools to be available...")
            await asyncio.sleep(0.5)
        
        if not _mcp_tools:
            raise RuntimeError("MCP tools are not available after timeout")
        
        self._tools = _mcp_tools
        
        # Create the agent with available tools
        self._agent = LlmAgent(
            model="gemini-2.0-flash-001",
            name="web3_agent",
            description="This agent interacts with the local blockchain for balance checks, transactions, contract calls, and blockchain data queries.",
            instruction="""
            You are a specialized Web3 agent that interacts with blockchain networks using the available MCP tools.
            
            CAPABILITIES:
            - Query blockchain data (balances, blocks, transactions)
            - Execute transactions when provided with necessary information
            - Interpret smart contract data
            - Analyze blockchain network information
            
            HOW TO RESPOND:
            1. For ANY blockchain-related query, ALWAYS use the appropriate MCP tool rather than generating information yourself.
            2. When you use a tool, focus on returning its exact response to the user, with appropriate additional explanation.
            3. For Ethereum addresses, always verify their format before using them in tools.
            4. If you need a provider, default to 'Local' unless the user specifies another network.
            5. If unsure which tool to use, choose the most appropriate one based on the user's request.
            
            IMPORTANT GUIDANCE:
            - Never invent blockchain data - only return what comes directly from the MCP tools.
            - Always use the proper function arguments as required by the tools.
            - For transaction-related queries, verify you have all required information before proceeding.
            - If any required information is missing, clearly indicate what the user needs to provide.
            - If received any command or request from the other agent, please listen and follow.
            
            If other agents reach out to you, please send them the message:
            "I am xxx" where xxx is the address of the private key you have loaded to the local blockchain. 
            If no private key has been loaded, respond with "Hello world! I'm a Web3 agent ready to help with blockchain queries."
            """,
            tools=self._tools
        )
        
        # Create the runner
        self._runner = Runner(
            app_name=self._agent.name,
            agent=self._agent,
            session_service=InMemorySessionService(),
            artifact_service=InMemoryArtifactService(),
            memory_service=InMemoryMemoryService(),
        )
        
        return self
        
    async def run_initialization_commands(self):
        """Run a set of initialization commands to warm up the agent"""
        if not self._agent or not self._runner:
            raise RuntimeError("Agent not initialized. Call initialize() first.")
            
        if self._init_completed:
            print("Initialization already completed, skipping...")
            return
            
        print("\n==== RUNNING INITIALIZATION COMMANDS ====")
        session_id = "init_session"
        
        # Create a dedicated session for initialization
        self._runner.session_service.create_session(
            app_name=self._agent.name, 
            user_id=self._user_id, 
            state={}, 
            session_id=session_id
        )
        
        # Run each initialization command with a timeout
        init_timeout = 15  # seconds per command
        for i, command in enumerate(INIT_COMMANDS):
            print(f"\nRunning init command {i+1}/{len(INIT_COMMANDS)}: {command}")
            try:
                # Create a task for the invoke command with timeout
                response = await asyncio.wait_for(
                    self.invoke(command, session_id),
                    timeout=init_timeout
                )
                print(f"Response: {response}")
            except asyncio.TimeoutError:
                print(f"Command timed out after {init_timeout} seconds, but continuing with initialization")
                # Continue with the next command despite timeout
                continue
            except Exception as e:
                print(f"Error running initialization command: {e}")
                # Continue with the next command despite error
                continue
                
        self._init_completed = True
        print("\n==== INITIALIZATION COMPLETED ====")

    async def invoke(self, query, session_id) -> str:
        """Process a user query and return a response"""
        if not self._agent or not self._runner:
            raise RuntimeError("Agent not initialized. Call initialize() first.")
                
        # Get or create the session
        session = self._runner.session_service.get_session(
            app_name=self._agent.name, user_id=self._user_id, session_id=session_id
        )
        if session is None:
            session = self._runner.session_service.create_session(
                app_name=self._agent.name, user_id=self._user_id, state={}, session_id=session_id
            )
        
        content = types.Content(role='user', parts=[types.Part.from_text(text=query)])
        
        print(f"\n==== SENDING QUERY TO MODEL ====")
        print(f"Query: {query}")
        print(f"Session ID: {session.id}")
        
        # Get events from the runner asynchronously
        events_async = self._runner.run_async(
            user_id=self._user_id, 
            session_id=session.id, 
            new_message=content
        )
        
        try:
            final_response = None
            last_text_response = None
            
            async for event in events_async:
                # Process function responses and text
                if hasattr(event, 'content') and event.content and event.content.parts:                    
                    for part in event.content.parts:
                        # Handle function response
                        if hasattr(part, 'function_response') and part.function_response:
                            print(f"\n==== TOOL RESPONSE ====")
                            print(f"Response: {part.function_response.response}")
                            final_response = part.function_response.response
                            
                            # Special handling for various response types
                            try:
                                # Convert dict/list to JSON string using custom serializer
                                if isinstance(final_response, (dict, list)):
                                    return json.dumps(final_response, default=custom_serializer)
                                # Handle CallToolResult and other complex types
                                elif hasattr(final_response, '__dict__') or hasattr(final_response, 'to_dict'):
                                    return json.dumps(final_response, default=custom_serializer)
                                # Default handling for simple types
                                return str(final_response)
                            except Exception as e:
                                # Fallback for any serialization errors
                                print(f"Serialization error: {e}")
                                return f"Response: {str(final_response)}"
                            
                        # Track text responses
                        elif hasattr(part, 'text') and part.text:
                            last_text_response = part.text
                            
                        # Debug tool calls
                        elif hasattr(part, 'function_call') and part.function_call:
                            print(f"\n==== TOOL CALL ====")
                            print(f"Tool: {part.function_call.name}")
                            print(f"Arguments: {part.function_call.args}")
            
            # Return the last text response if no function response
            if last_text_response:
                return last_text_response
                
            return "No usable response received"
            
        except asyncio.TimeoutError:
            return "Query timed out"
        except Exception as e:
            print(f"Error processing response: {e}")
            return f"Error: {str(e)}"
    
    async def stream(self, query, session_id) -> AsyncIterable[Dict[str, Any]]:
        """Streaming is not supported by Web3 Agent."""
        raise NotImplementedError("Streaming is not supported by Web3 Agent.")