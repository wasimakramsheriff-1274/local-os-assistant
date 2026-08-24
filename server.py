"""
Local OS & Second Brain MCP Server
==================================
FastMCP server exposing cross-platform system tools and a persistent SQLite memory brain over stdio transport.
"""

import os
import sys
import json
import sqlite3
import platform
import subprocess
import webbrowser
from datetime import datetime
from typing import List, Optional
import psutil
try:
    from fastmcp import FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP


# Initialize FastMCP Server
mcp = FastMCP("Local OS & Brain Server")

# Database Path Setup
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brain.db")


def init_db():
    """Initializes the SQLite database for long-term brain memory storage."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            tags TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """
    )
    conn.commit()
    conn.close()


# Ensure database is ready on module load
init_db()


# ------------------------------------------------------------------------------
# System Tools
# ------------------------------------------------------------------------------

@mcp.tool()
def read_local_file(file_path: str) -> str:
    """
    Safely reads and returns the text contents of a local file.

    Args:
        file_path: Absolute or relative path to the file to read.
    """
    try:
        abs_path = os.path.abspath(file_path)
        if not os.path.exists(abs_path):
            return f"Error: File '{abs_path}' does not exist."
        if not os.path.isfile(abs_path):
            return f"Error: Path '{abs_path}' is not a file."

        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return content
    except Exception as e:
        return f"Error reading file '{file_path}': {str(e)}"


@mcp.tool()
def write_local_file(file_path: str, content: str) -> str:
    """
    Writes content to a local file. Creates parent directories automatically if needed.

    Args:
        file_path: Absolute or relative path to the file to write.
        content: String content to write into the file.
    """
    try:
        abs_path = os.path.abspath(file_path)
        parent_dir = os.path.dirname(abs_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(content)

        return f"Successfully wrote {len(content)} characters to '{abs_path}'."
    except Exception as e:
        return f"Error writing file '{file_path}': {str(e)}"


@mcp.tool()
def list_directory(path: str = ".") -> str:
    """
    Inspects and lists contents of a directory with file sizes and type indicators.

    Args:
        path: Path to the directory to list (defaults to current working directory).
    """
    try:
        abs_path = os.path.abspath(path)
        if not os.path.exists(abs_path):
            return f"Error: Directory '{abs_path}' does not exist."
        if not os.path.isdir(abs_path):
            return f"Error: Path '{abs_path}' is not a directory."

        entries = os.listdir(abs_path)
        results = []
        for entry in sorted(entries):
            full_entry_path = os.path.join(abs_path, entry)
            if os.path.isdir(full_entry_path):
                results.append(f"[DIR]  {entry}/")
            else:
                size = os.path.getsize(full_entry_path)
                results.append(f"[FILE] {entry} ({size:,} bytes)")

        summary = f"Contents of '{abs_path}' ({len(entries)} items):\n"
        return summary + "\n".join(results)
    except Exception as e:
        return f"Error listing directory '{path}': {str(e)}"


@mcp.tool()
def execute_terminal_command(command: str, confirm_destructive: bool = False) -> str:
    """
    Executes a shell command on the local system with safety checks for destructive keywords.

    Args:
        command: The shell command line string to execute.
        confirm_destructive: Set to True if user explicitly approves running a potentially destructive command.
    """
    destructive_keywords = [
        "rm", "del", "rmdir", "format", "drop", "rd", "erase", "mkfs", "dd",
        "sudo rm", "remove-item", "del-item", "truncate", "shred"
    ]

    cmd_lower = command.lower()
    tokens = cmd_lower.split()

    is_destructive = any(
        kw in tokens or any(cmd_lower.startswith(kw + " ") for kw in destructive_keywords)
        for kw in destructive_keywords
    )

    if is_destructive and not confirm_destructive:
        return (
            f"SAFETY WARNING: Command '{command}' contains potentially destructive keywords. "
            f"To execute, re-run with confirm_destructive=True after explicit confirmation."
        )

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60
        )
        output = []
        if result.stdout:
            output.append(f"--- STDOUT ---\n{result.stdout.strip()}")
        if result.stderr:
            output.append(f"--- STDERR ---\n{result.stderr.strip()}")
        output.append(f"Exit Code: {result.returncode}")
        return "\n\n".join(output) if output else "Command executed with no output."
    except subprocess.TimeoutExpired:
        return "Error: Command execution timed out (60 seconds limit)."
    except Exception as e:
        return f"Error executing command: {str(e)}"


@mcp.tool()
def open_application_or_url(target: str) -> str:
    """
    Launches desktop applications or web URLs cross-platform (Windows, macOS, Linux).

    Args:
        target: Application executable name/path or web URL (e.g. 'https://google.com', 'notepad', 'calc').
    """
    try:
        # Check if target is a web URL
        if target.startswith("http://") or target.startswith("https://") or "www." in target:
            webbrowser.open(target)
            return f"Opened web URL: '{target}' in default browser."

        current_os = platform.system()

        if current_os == "Windows":
            os.startfile(target)
        elif current_os == "Darwin":  # macOS
            subprocess.Popen(["open", target])
        elif current_os == "Linux":
            subprocess.Popen(["xdg-open", target])
        else:
            subprocess.Popen([target])

        return f"Launched application/file: '{target}' on {current_os}."
    except Exception as e:
        return f"Error opening '{target}': {str(e)}"


@mcp.tool()
def get_system_metrics() -> str:
    """
    Returns real-time system metrics including CPU usage, RAM stats, disk usage, and battery status.
    """
    try:
        cpu_percent = psutil.cpu_percent(interval=0.5)
        cpu_count_logical = psutil.cpu_count(logical=True)
        cpu_count_physical = psutil.cpu_count(logical=False)

        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/") if platform.system() != "Windows" else psutil.disk_usage("C:\\")

        battery = psutil.sensors_battery()
        battery_info = "N/A"
        if battery:
            plugged = "Plugged in" if battery.power_plugged else "On battery"
            battery_info = f"{battery.percent}% ({plugged})"

        metrics = {
            "timestamp": datetime.now().isoformat(),
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "cpu": {
                "usage_percent": cpu_percent,
                "logical_cores": cpu_count_logical,
                "physical_cores": cpu_count_physical
            },
            "ram": {
                "total_gb": round(ram.total / (1024 ** 3), 2),
                "available_gb": round(ram.available / (1024 ** 3), 2),
                "used_gb": round(ram.used / (1024 ** 3), 2),
                "percent_used": ram.percent
            },
            "disk": {
                "total_gb": round(disk.total / (1024 ** 3), 2),
                "free_gb": round(disk.free / (1024 ** 3), 2),
                "used_gb": round(disk.used / (1024 ** 3), 2),
                "percent_used": disk.percent
            },
            "battery": battery_info
        }
        return json.dumps(metrics, indent=2)
    except Exception as e:
        return f"Error retrieving system metrics: {str(e)}"


# ------------------------------------------------------------------------------
# Memory & Brain Tools
# ------------------------------------------------------------------------------

@mcp.tool()
def save_memory(key: str, value: str, tags: Optional[List[str]] = None) -> str:
    """
    Stores user notes, project context, key decisions, or preferences to the local SQLite database ('brain.db').

    Args:
        key: Unique identifier/topic for this memory item (e.g. 'user_preference_editor', 'project_stack').
        value: Detailed memory text to store.
        tags: Optional list of descriptive tag strings for categorizing memory (e.g. ['preference', 'editor']).
    """
    try:
        tags_str = json.dumps(tags or [])
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO memories (key, value, tags, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                tags = excluded.tags,
                updated_at = CURRENT_TIMESTAMP
        """,
            (key, value, tags_str),
        )
        conn.commit()
        conn.close()
        return f"Memory successfully saved under key '{key}' with tags {tags or []}."
    except Exception as e:
        return f"Error saving memory: {str(e)}"


@mcp.tool()
def search_memory(query: str = "") -> str:
    """
    Retrieves stored context, past notes, and instructions from SQLite 'brain.db'.

    Args:
        query: Search term to match against memory keys, values, or tags. Pass empty string to list all items.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        if query.strip():
            wildcard = f"%{query.strip()}%"
            cursor.execute(
                """
                SELECT key, value, tags, updated_at FROM memories
                WHERE key LIKE ? OR value LIKE ? OR tags LIKE ?
                ORDER BY updated_at DESC
            """,
                (wildcard, wildcard, wildcard),
            )
        else:
            cursor.execute(
                """
                SELECT key, value, tags, updated_at FROM memories
                ORDER BY updated_at DESC LIMIT 20
            """
            )

        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return f"No memories found matching query: '{query}'" if query else "Brain memory is currently empty."

        results = []
        for key, value, tags_json, updated_at in rows:
            try:
                tags = json.loads(tags_json) if tags_json else []
            except Exception:
                tags = []
            results.append({
                "key": key,
                "value": value,
                "tags": tags,
                "last_updated": updated_at
            })

        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error searching memory: {str(e)}"


# ------------------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    # Runs the FastMCP server over stdio transport
    mcp.run(transport="stdio")
