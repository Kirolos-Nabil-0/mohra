import sys
import json
import argparse
from pathlib import Path
from typing import List, Dict, Optional

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

from config import load_config, save_config
from modules.sheet_parser import SheetParser
from modules.drive_downloader import DriveDownloader
from modules.docx_parser import DocxParser
from modules.progress import ProgressTracker
from modules.chrome_launcher import launch_chrome_for_automation, find_chrome_executable
from modules.readora_client import ReadoraClient

console = Console()

def display_banner(dry_run: bool):
    mode_text = "[bold yellow]DRY-RUN (Safe Mode - No DB Writes)[/bold yellow]" if dry_run else "[bold red]LIVE EXECUTION (Will Update Readora)[/bold red]"
    console.print(Panel.fit(
        f"[bold cyan]Mohra App Automation Tool[/bold cyan]\n"
        f"Google Drive → Docx Parser → Readora Lab Questions\n"
        f"Operating Mode: {mode_text}",
        border_style="cyan"
    ))

def list_stories(sheet_parser: SheetParser):
    with console.status("[bold green]Loading stories from sheet..."):
        stories = sheet_parser.parse_stories()

    table = Table(title=f"Stories Assigned to '{sheet_parser.assigned_to.title()}' (Total: {len(stories)})", show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", width=4)
    table.add_column("Grade/Sheet", width=12)
    table.add_column("Row", width=5)
    table.add_column("Story Title", style="cyan")
    table.add_column("Status / Comment", width=25)
    table.add_column("Drive Link", width=10)

    for i, s in enumerate(stories, 1):
        status_color = "green" if s["is_done"] else "yellow"
        has_link = "[green]Yes[/green]" if s["drive_url"] else "[red]No[/red]"
        table.add_row(
            str(i),
            s["sheet_name"],
            str(s["row_index"]),
            s["story_name"],
            f"[{status_color}]{s['comment'] or 'Pending'}[/{status_color}]",
            has_link
        )

    console.print(table)
    done_count = sum(1 for s in stories if s["is_done"])
    console.print(f"[bold]Summary:[/bold] [green]{done_count} Completed[/green] | [yellow]{len(stories) - done_count} Pending[/yellow]\n")

def process_single_story(story: Dict, config: dict, downloader: DriveDownloader, progress: ProgressTracker, client: Optional[ReadoraClient] = None) -> bool:
    story_name = story["story_name"]
    drive_url = story["drive_url"]
    dry_run = config.get("dry_run", True)

    console.print(f"\n[bold blue]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold blue]")
    console.print(f"[bold cyan]Processing Story:[/bold cyan] [bold white]{story_name}[/bold white] ({story.get('sheet_name', 'Sheet')} - Row {story.get('row_index', '?')})")

    if not drive_url:
        console.print(f"[red]Error:[/red] No Google Drive URL found for '{story_name}'")
        progress.record_failure(story, "No Drive URL")
        return False

    # 1. Download Docx
    preferred_lang = config.get("preferred_language_file", "First language")
    with console.status(f"[bold yellow]Downloading {preferred_lang}.docx from Google Drive..."):
        docx_path, status_msg = downloader.download_story_docx(drive_url, preferred_lang=preferred_lang)

    if not docx_path or not docx_path.exists():
        console.print(f"[red]Error:[/red] {status_msg}")
        progress.record_failure(story, status_msg)
        return False

    console.print(f"[green]✓[/green] Docx ready: [dim]{docx_path}[/dim] ({status_msg})")

    # 2. Parse Comprehension Questions
    questions = DocxParser.parse_comprehension_questions(docx_path)
    if not questions:
        console.print(f"[red]Error:[/red] No 'Comprehension Questions' could be parsed from {docx_path.name}")
        progress.record_failure(story, "No Comprehension Questions parsed")
        return False

    console.print(f"[green]✓[/green] Successfully parsed [bold]{len(questions)} Comprehension Questions[/bold]:")
    
    # Display preview table of parsed questions
    q_table = Table(show_header=True, header_style="bold blue")
    q_table.add_column("No.", width=4)
    q_table.add_column("Question", style="white")
    q_table.add_column("Choices (A, B, C, D)", style="dim")
    q_table.add_column("Ans", style="bold green", width=5)

    for q in questions:
        choices_str = " | ".join(f"{c['letter']}: {c['text'][3:]}" for c in q["choices"])
        q_table.add_row(str(q["num"]), q["raw_question"][:60], choices_str[:60] + "...", q["answer"])

    console.print(q_table)

    # 3. Readora Automation
    if client is None:
        console.print("[dim]No ReadoraClient active, skipping browser interaction.[/dim]")
        return True

    try:
        # Search book
        book_info = client.search_book(story_name)
        if not book_info:
            console.print(f"[red]Error:[/red] Book '{story_name}' not found on Readora Lab.")
            progress.record_failure(story, "Book not found on Readora")
            return False

        # Open edit
        client.open_book_edit(book_info)

        # Apply questions
        success = client.edit_questions(questions, dry_run=dry_run)
        if success:
            progress.record_success(story, len(questions), dry_run=dry_run)
            status_str = "[bold yellow]DRY-RUN SUCCESS[/bold yellow]" if dry_run else "[bold green]LIVE SUCCESS[/bold green]"
            console.print(f"{status_str}: Successfully processed '{story_name}'!\n")
            return True
        else:
            progress.record_failure(story, "Failed during question editing")
            return False

    except Exception as e:
        console.print(f"[red]Exception during Readora processing:[/red] {e}")
        progress.record_failure(story, str(e))
        return False

def run_automation_flow(stories: List[Dict], config: dict):
    if not stories:
        console.print("[yellow]No stories to process.[/yellow]")
        return

    downloader = DriveDownloader(cache_dir=config.get("cache_dir", "./cache"))
    progress = ProgressTracker()

    console.print(f"[bold]Starting Readora automation client for {len(stories)} stories...[/bold]")
    client = ReadoraClient(config)
    try:
        client.start_browser()
        client.login()

        dry_run = config.get("dry_run", True)
        for i, story in enumerate(stories, 1):
            if not dry_run and progress.is_processed(story["story_name"]):
                console.print(f"[dim][{i}/{len(stories)}] Skipping '{story['story_name']}' - already completed successfully.[/dim]")
                continue
            console.print(f"\n[bold]Processing [{i}/{len(stories)}][/bold]")
            process_single_story(story, config, downloader, progress, client=client)

    except KeyboardInterrupt:
        console.print("\n[yellow]Automation paused by user.[/yellow]")
    finally:
        client.close()
        console.print("[dim]Readora automation client closed.[/dim]")

def interactive_menu():
    config = load_config()
    downloader = DriveDownloader(cache_dir=config.get("cache_dir", "./cache"))
    sheet_parser = SheetParser(
        sheet_url=config.get("sheet_url", ""),
        assigned_to=config.get("assigned_to", "Mohra"),
        cache_dir=config.get("cache_dir", "./cache")
    )
    progress = ProgressTracker()

    while True:
        display_banner(config.get("dry_run", True))
        console.print("  [bold cyan]1.[/bold cyan] List All Assigned Stories & Status")
        console.print("  [bold cyan]2.[/bold cyan] Process All Pending / Uncompleted Stories")
        console.print("  [bold cyan]3.[/bold cyan] Process a Specific Story by Name")
        console.print("  [bold cyan]4.[/bold cyan] Test Docx Parsing Only (Local / Drive)")
        console.print("  [bold cyan]5.[/bold cyan] Launch Chrome with Google Profile (`mohrawagdy58@gmail.com`)")
        console.print("  [bold cyan]6.[/bold cyan] Toggle Dry-Run Mode")
        console.print("  [bold cyan]7.[/bold cyan] View Settings")
        console.print("  [bold cyan]8.[/bold cyan] Exit\n")

        choice = Prompt.ask("Select an option", choices=["1", "2", "3", "4", "5", "6", "7", "8"], default="1")

        if choice == "1":
            list_stories(sheet_parser)
        elif choice == "2":
            pending = sheet_parser.get_uncompleted_stories()
            console.print(f"Found [bold yellow]{len(pending)}[/bold yellow] pending stories.")
            if pending:
                if Confirm.ask(f"Do you want to process all {len(pending)} pending stories?", default=True):
                    run_automation_flow(pending, config)
        elif choice == "3":
            query = Prompt.ask("Enter story name (or part of it)")
            story = sheet_parser.find_story(query)
            if story:
                console.print(f"Found: [bold cyan]{story['story_name']}[/bold cyan] ({story['sheet_name']} - Row {story['row_index']})")
                if Confirm.ask("Process this story?", default=True):
                    run_automation_flow([story], config)
            else:
                console.print(f"[red]No story matching '{query}' found.[/red]")
        elif choice == "4":
            query = Prompt.ask("Enter story name to parse", default="The Friendly Polar Bear")
            story = sheet_parser.find_story(query)
            if story:
                process_single_story(story, config, downloader, progress, client=None)
            else:
                console.print(f"[red]Story '{query}' not found.[/red]")
        elif choice == "5":
            from modules.google_auth import GoogleAuthenticator
            ga = GoogleAuthenticator(config)
            ga.launch_and_login(headless=False)
        elif choice == "6":
            config["dry_run"] = not config.get("dry_run", True)
            save_config(config)
            state = "[bold yellow]ENABLED (Safe)[/bold yellow]" if config["dry_run"] else "[bold red]DISABLED (Live)[/bold red]"
            console.print(f"Dry-run mode is now {state}\n")
        elif choice == "7":
            console.print(Panel(json.dumps(config, indent=2), title="Current Configuration"))
        elif choice == "8":
            console.print("[dim]Goodbye![/dim]")
            sys.exit(0)

def main():
    parser = argparse.ArgumentParser(description="Mohra App Automation Tool")
    parser.add_argument("--story", type=str, help="Process a specific story by name")
    parser.add_argument("--all", action="store_true", help="Process all pending stories")
    parser.add_argument("--list", action="store_true", help="List all assigned stories and exit")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode (no live writes)")
    parser.add_argument("--live", action="store_true", help="Force live execution mode (writes to Readora)")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")

    args = parser.parse_args()
    config = load_config()

    if args.dry_run:
        config["dry_run"] = True
    elif args.live:
        config["dry_run"] = False
    if args.headless:
        config["headless"] = True

    sheet_parser = SheetParser(
        sheet_url=config.get("sheet_url", ""),
        assigned_to=config.get("assigned_to", "Mohra"),
        cache_dir=config.get("cache_dir", "./cache")
    )

    if args.list:
        list_stories(sheet_parser)
        return

    if args.story:
        story = sheet_parser.find_story(args.story)
        if not story:
            console.print(f"[red]Story '{args.story}' not found.[/red]")
            sys.exit(1)
        run_automation_flow([story], config)
        return

    if args.all:
        pending = sheet_parser.get_uncompleted_stories()
        run_automation_flow(pending, config)
        return

    # Default to interactive menu
    interactive_menu()

if __name__ == "__main__":
    main()
