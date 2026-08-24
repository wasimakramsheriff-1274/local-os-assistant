# Local OS Assistant & Second Brain (MCP & OpenAI)

An autonomous, cross-platform local desktop assistant powered by the **Model Context Protocol (MCP)** and **OpenAI (`gpt-4o`)**. It executes local system commands safely, reads and writes files, monitors hardware diagnostics, and remembers cross-session project context using a local SQLite database (`brain.db`).

---

## Features

- **MCP FastMCP Server (`server.py`)**:
  - `read_local_file`: Reads text contents of local files safely.
  - `write_local_file`: Writes content to local files, creating missing directories automatically.
  - `list_directory`: Formatted directory inspection with size and file type details.
  - `execute_terminal_command`: Runs shell commands with safety prompt guardrails for destructive keywords (`rm`, `del`, `rmdir`, `format`, `drop`).
  - `open_application_or_url`: Launches desktop applications or opens URLs in default web browser cross-platform (Windows, macOS, Linux).
  - `get_system_metrics`: Retrieves real-time CPU, RAM, Disk, and Battery diagnostics using `psutil`.
  - `save_memory`: Persists user notes, project stack context, and preferences into SQLite `brain.db`.
  - `search_memory`: Full-text/pattern searching across stored memories.
- **Interactive Terminal Assistant (`client.py`)**:
  - Dynamic tool discovery via MCP stdio transport.
  - Startup memory hydration from `brain.db`.
  - Persistent CLI REPL prompt (`You > ` / `Brain > `).
- **Claude Desktop Ready (`claude_desktop_config.json`)**:
  - Ready-to-use configuration file for integration with Claude Desktop.

---

## Installation & Setup

### Prerequisites
- Python 3.10 or higher.
- An OpenAI API Key (`OPENAI_API_KEY`).

### 1. Set Up Virtual Environment

Navigate to the project root directory and create a virtual environment:

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 2. Install Dependencies

Install pinned dependencies from `requirements.txt`:

```bash
pip install -r requirements.txt
```

### 3. Environment Configuration

Create a `.env` file in the project root directory or set the environment variable:

```env
OPENAI_API_KEY=your_openai_api_key_here
```

---

## Running the Assistant

### Option A: Interactive CLI Client

Start the interactive assistant terminal:

```bash
python client.py
```

Example session:
```text
You > Save a memory under key 'favorite_editor' value 'VS Code with Vim binding' tags ['preference', 'tools']
Brain > I've stored your preference for VS Code with Vim binding in the brain database.

You > What are my current system metrics?
Brain > Your system is running at 15.4% CPU usage with 12.2 GB / 32 GB RAM used and 78% battery remaining.
```

### Option B: Integrate with Claude Desktop

To use this local MCP server with **Claude Desktop**, add the configuration from `claude_desktop_config.json` to your Claude Desktop configuration file:

- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

Add the server definition:

```json
{
  "mcpServers": {
    "local-os-brain": {
      "command": "python",
      "args": [
        "C:/Users/acer/.gemini/antigravity-ide/scratch/mcp_local_brain/server.py"
      ]
    }
  }
}
```

*Note: Replace `"python"` with the absolute path to your virtual environment Python executable if using a venv.*

---

## Project Structure

```text
mcp_local_brain/
├── brain.db                      # SQLite database auto-generated on first run
├── server.py                    # FastMCP Server with OS & Brain Tools
├── client.py                    # Interactive Terminal AI Assistant
├── claude_desktop_config.json   # Claude Desktop configuration snippet
├── requirements.txt             # Pinned python dependencies
└── README.md                    # Documentation & Setup Guide
```

---

## License
MIT
