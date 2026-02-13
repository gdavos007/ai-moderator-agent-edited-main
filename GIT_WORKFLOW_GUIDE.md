# Git Workflow Guide for AI Moderator Agent

## Table of Contents
1. [Understanding Git Basics](#understanding-git-basics)
2. [Main Branch Workflow (Production Code)](#main-branch-workflow-production-code)
3. [Dev Branch Workflow (Experimental Changes)](#dev-branch-workflow-experimental-changes)
4. [Merging Dev to Main](#merging-dev-to-main)
5. [Common Scenarios](#common-scenarios)
6. [Troubleshooting](#troubleshooting)

---

## Understanding Git Basics

### What is Git?
Git tracks changes to your code over time. Think of it like a save system in a video game with multiple save slots.

### Key Concepts:
- **Repository (repo)**: Your project folder tracked by Git
- **Branch**: A parallel version of your code (like alternate timelines)
- **Commit**: A saved snapshot of your code at a point in time
- **Remote**: The version stored on GitHub (cloud backup)
- **Local**: The version on your computer

### Your Repository Location:
```
/Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent
```

### Your Remote (GitHub):
```
https://github.com/sreraoai/ai-moderator-agent.git
```

---

## Main Branch Workflow (Production Code)

The `main` branch should contain **only stable, tested, production-ready code**.

### 1. Switch to Main Branch

```bash
cd /Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent
git checkout main
```

**What this does**: Switches your working directory to the main branch.

### 2. Check Current Status

```bash
git status
```

**What you'll see**:
- Modified files (in red)
- Files ready to commit (in green)
- Current branch name
- Whether you're ahead/behind remote

### 3. See What Changed

```bash
git diff
```

**What this does**: Shows line-by-line changes in your files (not yet staged).

### 4. Stage Your Changes

```bash
# Stage specific files
git add agent.py
git add config/agent_config.py

# OR stage all changes at once
git add .
```

**What this does**: Tells Git which changes you want to include in the next commit.
- Think of it as putting items in a shopping cart before checkout.

### 5. Commit Your Changes (Save Locally)

```bash
git commit -m "Brief description of what you changed"
```

**Example**:
```bash
git commit -m "Fix microphone initialization bug"
```

**What this does**:
- Creates a snapshot of your staged changes
- Saves it ONLY on your local computer
- Does NOT send to GitHub yet

**Best practices for commit messages**:
- Use present tense: "Fix bug" not "Fixed bug"
- Be specific: "Add audio track subscription" not "Update code"
- Keep it under 50 characters

### 6. Pull Latest Changes from GitHub (Sync Down)

```bash
git pull origin main
```

**What this does**:
- Downloads any new commits from GitHub
- Merges them with your local changes
- Prevents conflicts when pushing

**Why you need this**:
- Someone else (or you from another computer) might have pushed changes
- Ensures you're working with the latest code

### 7. Push Your Changes to GitHub (Sync Up)

```bash
git push origin main
```

**What this does**:
- Uploads your local commits to GitHub
- Makes your changes visible to others
- Backs up your work to the cloud

**Breaking it down**:
- `push`: Upload commits
- `origin`: The nickname for your GitHub repository
- `main`: Which branch to push to

### Complete Main Branch Workflow Example

```bash
# 1. Start on main branch
cd /Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent
git checkout main

# 2. Check what changed
git status
git diff

# 3. Stage your changes
git add .

# 4. Commit locally
git commit -m "Fix audio track subscription for multi-participant"

# 5. Pull latest from GitHub (important!)
git pull origin main

# 6. Push to GitHub
git push origin main
```

---

## Dev Branch Workflow (Experimental Changes)

The `dev` branch is for **experimental, untested, work-in-progress code**.

### One-Time Setup: Create Dev Branch

```bash
cd /Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent

# Create dev branch from current main
git checkout main
git checkout -b dev

# Push dev branch to GitHub
git push -u origin dev
```

**What `-u origin dev` does**:
- `-u`: Sets up tracking (links your local dev to remote dev)
- `origin dev`: Push to the dev branch on GitHub

### 1. Switch to Dev Branch

```bash
git checkout dev
```

**What you'll see**:
```
Switched to branch 'dev'
```

**How to verify you're on dev**:
```bash
git branch
```
Output will show `*` next to current branch:
```
* dev
  main
```

### 2. Make Experimental Changes

Edit your files freely. This won't affect `main` at all!

### 3. Check What Changed

```bash
git status
git diff
```

### 4. Stage and Commit Changes

```bash
git add .
git commit -m "Experiment: Try new STT model configuration"
```

### 5. Push to Dev Branch on GitHub (Optional)

```bash
git push origin dev
```

**When to push dev to GitHub**:
- ✅ If you want to backup your experiments
- ✅ If you want to share with collaborators
- ✅ If you're switching computers
- ❌ Not required for local experimentation

### 6A. Discard Experimental Changes (If Failed)

**Option 1: Discard uncommitted changes**
```bash
# See what would be discarded
git status

# Discard all changes to tracked files
git checkout .

# Remove untracked files (new files you created)
git clean -fd
```

**Option 2: Discard committed changes (go back in time)**
```bash
# See commit history
git log --oneline

# Go back to a specific commit (loses all commits after it)
git reset --hard abc123  # Replace abc123 with commit hash

# OR go back to match remote dev exactly
git reset --hard origin/dev
```

**Option 3: Delete dev branch and start fresh**
```bash
# Switch to main first
git checkout main

# Delete local dev branch
git branch -D dev

# Create new dev branch from main
git checkout -b dev
```

### 6B. Keep Experimental Changes (If Successful)

If your experiment works, merge it to main! (See next section)

---

## Merging Dev to Main

When your experimental code in `dev` is **stable and tested**, merge it into `main`.

### The Merge Process Explained

**What merging does**:
- Takes commits from `dev` branch
- Applies them to `main` branch
- Creates a "merge commit" that combines both histories

**Think of it like**: Copying your draft document into the final version.

### Step-by-Step Merge

```bash
# 1. Make sure dev is committed
git checkout dev
git status  # Should show "nothing to commit"

# If you have uncommitted changes:
git add .
git commit -m "Finalize experimental feature"

# 2. Switch to main branch
git checkout main

# 3. Pull latest main from GitHub (important!)
git pull origin main

# 4. Merge dev into main
git merge dev
```

### What Happens During Merge?

**Scenario 1: Clean Merge (No Conflicts)**
```
Auto-merging agent.py
Merge made by the 'recursive' strategy.
 agent.py | 10 +++++++++-
 1 file changed, 9 insertions(+), 1 deletion(-)
```
✅ Success! Continue to step 5.

**Scenario 2: Merge Conflict**
```
Auto-merging agent.py
CONFLICT (content): Merge conflict in agent.py
Automatic merge failed; fix conflicts and then commit the result.
```

**How to fix conflicts**:
```bash
# 1. Open the conflicted file in your editor
# Look for markers like:
# <<<<<<< HEAD
# (your main branch code)
# =======
# (your dev branch code)
# >>>>>>> dev

# 2. Edit the file to keep the code you want
# Remove the <<<<<<, =======, >>>>>>> markers

# 3. Stage the resolved file
git add agent.py

# 4. Complete the merge
git commit -m "Merge dev into main - resolved conflicts"
```

### 5. Push Merged Main to GitHub

```bash
git push origin main
```

**Why you must push**:
- The merge only happened on your local computer
- GitHub doesn't know about it yet
- `git push` uploads the merged result to GitHub

### 6. Update Dev Branch (Optional but Recommended)

After merging dev to main, your dev branch is "behind" main.

```bash
# Switch back to dev
git checkout dev

# Update dev to match main
git merge main

# Push updated dev to GitHub
git push origin dev
```

**Why do this**:
- Keeps dev and main in sync
- Makes future merges easier
- Prevents complicated conflicts

### Complete Merge Workflow Example

```bash
# Starting point: You've been working in dev branch

# 1. Commit final dev changes
git checkout dev
git add .
git commit -m "Complete new feature testing"

# 2. Switch to main and update it
git checkout main
git pull origin main

# 3. Merge dev into main
git merge dev

# 4. Push merged main to GitHub
git push origin main

# 5. Update dev to match main
git checkout dev
git merge main
git push origin dev

# 6. Continue working in dev for next experiment
# (You're already on dev branch)
```

---

## Common Scenarios

### Scenario 1: "I Made Changes Directly in Main by Accident"

```bash
# If not yet committed:
# 1. Stash the changes temporarily
git stash

# 2. Switch to dev
git checkout dev

# 3. Apply the stashed changes
git stash pop

# 4. Now commit in dev
git add .
git commit -m "Experimental change (moved from main)"
```

### Scenario 2: "I Want to See What's Different Between Dev and Main"

```bash
# Show files that differ
git diff main..dev --name-only

# Show detailed differences
git diff main..dev
```

### Scenario 3: "I Want to Test Main Code Without Affecting Dev"

```bash
# Switch to main
git checkout main

# Make temporary changes and test
# ... test ...

# Discard temporary changes when done
git checkout .
```

### Scenario 4: "I Pushed to Main but It Was Broken!"

```bash
# Find the last good commit
git log --oneline

# Reset to that commit (e.g., abc123)
git reset --hard abc123

# Force push to GitHub (use with caution!)
git push --force origin main
```

⚠️ **Warning**: `--force` rewrites history. Only do this if you're the only one using the repo!

### Scenario 5: "I Want to Create a New Feature Branch"

```bash
# Start from main
git checkout main
git pull origin main

# Create feature branch
git checkout -b feature/audio-improvements

# Work on it...
git add .
git commit -m "Improve audio handling"

# Merge when ready
git checkout main
git merge feature/audio-improvements
git push origin main

# Delete feature branch when done
git branch -d feature/audio-improvements
```

---

## Troubleshooting

### "Your branch is ahead of 'origin/main' by X commits"

**Meaning**: You have local commits not yet pushed to GitHub.

**Fix**:
```bash
git push origin main
```

### "Your branch is behind 'origin/main' by X commits"

**Meaning**: GitHub has commits you don't have locally.

**Fix**:
```bash
git pull origin main
```

### "Your branch and 'origin/main' have diverged"

**Meaning**: Both local and GitHub have different commits.

**Fix**:
```bash
# Option 1: Merge remote changes
git pull origin main

# Option 2: Rebase (advanced - rewrites history)
git pull --rebase origin main
```

### "fatal: Not a git repository"

**Meaning**: You're not in the git directory.

**Fix**:
```bash
cd /Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent
```

### "error: failed to push some refs"

**Meaning**: Remote has changes you don't have.

**Fix**:
```bash
git pull origin main
git push origin main
```

### "Everything up-to-date" when pushing

**Meaning**: No new commits to push (you may have forgotten to commit).

**Fix**:
```bash
git status  # Check if you have uncommitted changes
git add .
git commit -m "Your message"
git push origin main
```

---

## Quick Reference Cheat Sheet

### Essential Commands

```bash
# Check status
git status

# See changes
git diff

# Stage changes
git add .

# Commit
git commit -m "Message"

# Push to GitHub
git push origin main

# Pull from GitHub
git pull origin main

# Switch branches
git checkout main
git checkout dev

# Create new branch
git checkout -b branch-name

# Merge branch
git merge branch-name

# See commit history
git log --oneline

# Discard uncommitted changes
git checkout .
```

### Branch Management

```bash
# List all branches
git branch -a

# Create branch
git checkout -b new-branch

# Delete branch
git branch -d branch-name

# Rename current branch
git branch -m new-name
```

### Undo Operations

```bash
# Undo last commit (keep changes)
git reset --soft HEAD~1

# Undo last commit (discard changes)
git reset --hard HEAD~1

# Discard all uncommitted changes
git checkout .
git clean -fd
```

---

## Visual Workflow Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                         GitHub (Remote)                      │
│                    origin/main  origin/dev                   │
└────────────▲──────────────────────▲─────────────────────────┘
             │                      │
             │ git push             │ git push
             │ origin main          │ origin dev
             │                      │
┌────────────┴──────────────────────┴─────────────────────────┐
│                    Your Computer (Local)                     │
│                                                              │
│  ┌──────────────┐              ┌──────────────┐            │
│  │     main     │◄─── merge ───│     dev      │            │
│  │  (stable)    │              │(experimental)│            │
│  └──────────────┘              └──────────────┘            │
│         ▲                              ▲                     │
│         │                              │                     │
│         └─── git checkout main/dev ────┘                    │
│                                                              │
│  Working Directory: Your actual files                       │
│  ┌────────────────────────────────────────────┐            │
│  │  agent.py, config/, src/, etc.             │            │
│  │  (Edit files here)                         │            │
│  └────────────────────────────────────────────┘            │
└──────────────────────────────────────────────────────────────┘
```

---

## Best Practices

1. **Commit Often**: Small, frequent commits are better than large ones
2. **Write Good Messages**: Future you will thank present you
3. **Pull Before Push**: Always `git pull` before `git push`
4. **Keep Main Clean**: Only merge tested code into main
5. **Use Dev for Experiments**: Try risky changes in dev first
6. **Don't Force Push**: Unless you're absolutely sure
7. **Check Status Frequently**: `git status` is your friend

---

## Getting Help

```bash
# Help for any command
git help commit
git help merge
git help branch

# Or use --help flag
git commit --help
```

---

Last Updated: 2024-11-27
Repository: /Users/srinivasmagidewar/My_Docs/ai_projects/ai-moderator-agent
