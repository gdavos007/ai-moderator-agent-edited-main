# How to Get Real Participant Names (Not Pre-assigned)

## Problem

Your current setup uses **pre-assigned names** like:
- "Alice Johnson"
- "Bob Smith"
- "Carol White"

You want participants to **enter their OWN real names** instead.

---

## ✅ Solution: Use `join_survey_custom_names.py`

This new script lets participants enter their real names when joining!

### Quick Start

**Terminal 1** - Start agent:
```bash
python3 agent.py dev
```

**Terminal 2** - Create survey with custom names:
```bash
python3 join_survey_custom_names.py --participants 3
```

---

## 🎬 How It Works

### Step 1: Script Generates Generic Links

```bash
python3 join_survey_custom_names.py --participants 2
```

Output:
```
🔗 PARTICIPANT 1 LINK:
https://meet.livekit.io/?url=wss://...&token=...

👤 Instructions:
   1. Click the link above
   2. You'll be asked: 'What's your name?'
   3. Type your REAL name (e.g., 'John Smith')
   4. Click 'Join'
```

### Step 2: Participant Clicks Link

When they click the link, LiveKit Meet shows:

```
┌──────────────────────────────────────┐
│  Join AI Survey                      │
│                                      │
│  What's your name?                   │
│  ┌────────────────────────────────┐ │
│  │ John Smith                     │ │  ← They type their name here!
│  └────────────────────────────────┘ │
│                                      │
│  [ Join Meeting ]                    │
└──────────────────────────────────────┘
```

### Step 3: They Join with Real Name

After clicking "Join Meeting", they enter the room as **"John Smith"** (their actual name!)

### Step 4: Agent Uses Real Name

Your agent sees:
```python
participant.identity = "John Smith"  # ✅ Real name!
```

Agent says:
```
"John Smith, what is your opinion on..."
```

### Step 5: CSV Export Has Real Names

```csv
Participant,Question,Response
John Smith,"What is your opinion?","I think..."
Sarah Johnson,"What is your opinion?","My view is..."
```

✅ **Real names in your data!**

---

## 🔄 Comparison

### Old Way (`join_survey_multi.py`)

```python
# Pre-assigns names in code
default_names = ["Alice Johnson", "Bob Smith", "Carol White"]

# Token created with pre-set identity
token.with_identity("Alice Johnson")  # ❌ Hardcoded!
```

**Result**: Everyone gets a fake name, regardless of who they are.

### New Way (`join_survey_custom_names.py`)

```python
# Uses placeholder identity
placeholder_id = f"participant-{random_id}"

# Token allows custom name entry
token.with_identity(placeholder_id)  # ✅ Placeholder!
```

**Result**: LiveKit Meet prompts each person to enter their real name.

---

## 📋 Different Methods to Get Real Names

### **Method 1: LiveKit Meet Name Prompt** (Recommended - Just Implemented!)

✅ **Pros:**
- Works out of the box
- Simple for participants (just click and enter name)
- No extra code needed
- Already implemented in `join_survey_custom_names.py`

❌ **Cons:**
- Requires participants to type their name correctly
- No validation

**Use**: `python3 join_survey_custom_names.py`

---

### **Method 2: Pre-Registration Web Form**

You could build a simple web form where participants register first:

```html
<form action="/register" method="POST">
  Name: <input type="text" name="participant_name">
  Email: <input type="email" name="email">
  <button>Register for Survey</button>
</form>
```

Then generate personalized join links with their names:
```python
token.with_identity(registered_name)  # From the form
```

✅ **Pros:**
- Can validate names
- Can collect additional info (email, etc.)
- Can send personalized links via email

❌ **Cons:**
- Requires building a registration system
- More complex setup

---

### **Method 3: Email-Based Invitations**

Generate unique links for each participant and email them:

```python
participants = [
    {"name": "John Doe", "email": "john@example.com"},
    {"name": "Jane Smith", "email": "jane@example.com"},
]

for participant in participants:
    token = create_token(participant["name"])
    send_email(participant["email"], join_url)
```

✅ **Pros:**
- Know exactly who's who
- Professional setup
- Can track who joined

❌ **Cons:**
- Requires email list upfront
- Need email sending capability

---

### **Method 4: URL Parameters**

Let participants enter names via URL:

```
https://your-site.com/join?name=John+Smith
```

Your web app reads the `name` parameter and creates a token:

```python
name = request.args.get('name')
token.with_identity(name)
```

✅ **Pros:**
- Simple to implement
- Shareable links

❌ **Cons:**
- Anyone can change the name in URL
- No validation

---

## 🎯 Recommended Approach for Your Use Case

**Use Method 1: `join_survey_custom_names.py`** (already created!)

**Why?**
- ✅ Works immediately (no additional code)
- ✅ Simple for participants
- ✅ Real names captured automatically
- ✅ No registration system needed
- ✅ Privacy-friendly (no pre-registration required)

**Perfect for:**
- Ad-hoc surveys
- Small to medium groups
- When you don't know participants in advance
- Quick testing

---

## 🚀 Quick Test

Try it now!

**Terminal 1:**
```bash
python3 agent.py dev
```

**Terminal 2:**
```bash
python3 join_survey_custom_names.py --participants 1
```

**Browser:**
1. Click the join link shown
2. When prompted "What's your name?", type YOUR real name
3. Click Join
4. Watch the agent greet you by YOUR name!

---

## 📝 Example Session

```
$ python3 join_survey_custom_names.py --participants 2

🔗 PARTICIPANT 1 LINK:
https://meet.livekit.io/?url=...&token=...

🔗 PARTICIPANT 2 LINK:
https://meet.livekit.io/?url=...&token=...
```

**Participant 1 joins as:** "Emily Rodriguez"
**Participant 2 joins as:** "Michael Chen"

**Agent says:**
```
"Hello everyone! I'm your AI survey moderator..."
"Emily Rodriguez, what is your opinion on...?"
[Emily responds]
"Thank you, Emily!"
"Michael Chen, what is your opinion on...?"
[Michael responds]
```

**CSV Output:**
```csv
Participant,Question,Response
Emily Rodriguez,"What is your opinion?","I believe..."
Michael Chen,"What is your opinion?","In my view..."
```

✅ **Real names captured!**

---

## 🔧 Troubleshooting

**Q: Participant still sees "Alice Johnson"**

A: You're using the old script. Use:
```bash
python3 join_survey_custom_names.py
```
Not:
```bash
python3 join_survey_multi.py  # Old script with pre-assigned names
```

**Q: Name prompt doesn't appear**

A: Make sure you're using the standard LiveKit Meet URL:
```
https://meet.livekit.io/?url=...&token=...
```
Not the custom URL:
```
https://meet.livekit.io/custom?liveKitUrl=...  # This skips name prompt
```

**Q: Want to pre-fill the name but allow editing?**

A: You can pass a suggested name in the URL:
```
https://meet.livekit.io/?url=...&token=...&username=Suggested+Name
```

---

## 📊 Summary

| Method | Complexity | Real Names | Use Case |
|--------|-----------|------------|----------|
| **join_survey_custom_names.py** | ✅ Easy | ✅ Yes | **Recommended** - Ad-hoc surveys |
| join_survey_multi.py | ✅ Easy | ❌ No | Testing with fake names |
| Web Form Registration | ⚠️ Medium | ✅ Yes | Large organized surveys |
| Email Invitations | ⚠️ Medium | ✅ Yes | Known participant list |
| URL Parameters | ⚠️ Medium | ⚠️ Maybe | Custom web apps |

**Bottom line:** Use `join_survey_custom_names.py` - it's the easiest way to get real participant names! 🎉
