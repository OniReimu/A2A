import os
import sys
# Get the path to A2A/samples/python
base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(base_path)

from common.server import A2AServer
from common.types import AgentCard, AgentCapabilities, AgentSkill, MissingAPIKeyError
from task_manager import AgentTaskManager
from agent import Web3Agent
import click
import logging
import asyncio
import threading
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@click.command()
@click.option("--host", default="localhost", help="Host address to bind the server to")
@click.option("--port", default=12345, help="Port to run the server on")
def cli(host, port):
    """Start the Web3 Agent server with command line options"""
    try:
        # Initialize in the main thread with asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Run the initialization part in this loop
        server = loop.run_until_complete(setup_server(host, port))
        
        # Start the server in a separate thread
        logger.info(f"Starting server at http://{host}:{port}")
        server_thread = threading.Thread(target=server.start)
        server_thread.daemon = True
        server_thread.start()
        
        # Keep the main thread alive until interrupted
        try:
            server_thread.join()
        except KeyboardInterrupt:
            logger.info("Server stopped by user")
            
    except MissingAPIKeyError as e:
        logger.error(f"Error: {e}")
        exit(1)
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        exit(1)

async def setup_server(host, port):
    """Set up the A2A server with the Web3 agent"""
    try:
        # Validate required environment variables
        if not os.getenv("GOOGLE_API_KEY"):
            raise MissingAPIKeyError("GOOGLE_API_KEY environment variable not set.")
        
        # Create agent card with capabilities
        capabilities = AgentCapabilities(streaming=False)
        skill = AgentSkill(
            id="web3_agent",
            name="Web3 Agent",
            description="A web3 agent that can interact with the local blockchain",
            tags=["web3", "blockchain", "ethereum"],
            examples=[
                "What is the balance of the account?", 
                "Can you load the local private key and send 0.1 ETH to the address 0x1234567890123456789012345678901234567890?",
                "What is the current block number?"
            ],
        )
        agent_card = AgentCard(
            name="Web3 Agent",
            description="This agent interacts with the local blockchain for balance checks, transactions, and blockchain queries",
            url=f"http://{host}:{port}",
            version="1.0.0",
            defaultInputModes=Web3Agent.SUPPORTED_CONTENT_TYPES,
            defaultOutputModes=Web3Agent.SUPPORTED_CONTENT_TYPES,
            capabilities=capabilities,
            skills=[skill],
        )

        # Create and initialize the Web3Agent
        logger.info("Initializing Web3 agent...")
        agent = Web3Agent()
        await agent.initialize()
        
        # Create the task manager with the initialized agent
        logger.info("Creating task manager...")
        task_manager = await AgentTaskManager.create(agent=agent)
        
        # Create the server without starting it
        server = A2AServer(
            agent_card=agent_card,
            task_manager=task_manager,
            host=host,
            port=port,
        )

        # Keep a reference to the event loop
        setattr(server, "_event_loop", asyncio.get_event_loop())
        logger.info("Server setup complete")
        
        return server
        
    except Exception as e:
        logger.error(f"An error occurred during server setup: {e}")
        raise

if __name__ == "__main__":
    cli()  # Run the Click command