"""Git utility functions — pure wrappers around the git CLI."""

import subprocess


def run_git_command(args, cwd=None):
    """Run a git command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            ['git', '-c', 'core.quotePath=false'] + args,
            cwd=cwd,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=30
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 1, "", "Git not found. Please install git."
    except subprocess.TimeoutExpired:
        return 1, "", "Command timed out"
    except Exception as e:
        return 1, "", str(e)
