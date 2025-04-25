## Web3 Agent

This sample uses the Agent Development Kit (ADK) to create a Web3 agent that is hosted as an A2A server.

This agent can interact with the local blockchain, perform operations like checking account balances and sending transactions.

## Prerequisites

- Python 3.9 or higher
- [UV](https://docs.astral.sh/uv/)
- Access to an LLM and API Key


## Running the Sample

1. Navigate to the samples directory:
    ```bash
    cd samples/python/agents/google_adk_mcp
    ```
2. Create an environment file with your API key:

   ```bash
   echo "GOOGLE_API_KEY=your_api_key_here" > .env
   ```

4. Run an agent:
    ```bash
    uv run .
    ```

5. In a separate terminal, run the A2A client:
    ```
    # Connect to the agent (specify the agent URL with correct port)
    cd samples/python/hosts/cli
    uv run . --agent http://localhost:12345

    # If you changed the port when starting the agent, use that port instead
    # uv run . --agent http://localhost:YOUR_PORT
    ```
