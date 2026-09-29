# LMS Agentic AI

An AI-powered Learning Management System built with Django and PostgreSQL, enhanced with Agentic AI capabilities for personalized student support and intelligent instructor workflows.

The platform combines a traditional LMS with two specialized AI agents — a **Student Agent** and an **Instructor Agent** — alongside a separate read-only **Course Chatbot**.

The system is designed to remain functional even when the external AI service is unavailable by using a built-in rule-based NLU and fallback mechanisms.

---

## Overview

The platform provides a complete learning environment for students and instructors.

Students can access courses, lessons, learning materials, quizzes, study plans, practice tasks, analytics, and AI-powered tutoring.

Instructors can manage their courses and learning materials, create and grade quizzes, analyze student performance, generate course summaries, identify at-risk students, and communicate with enrolled students.

The Agentic AI layer allows users to interact with these capabilities through natural-language commands while preserving application-level permissions, ownership checks, and action logging.

---

## Key Features

### Student Features

* Student registration and authentication
* Student dashboard
* Course and lesson access
* Learning material access
* PDF, DOCX, PPTX, and TXT materials
* Video lecture playback
* MCQ quizzes
* Essay / short-answer questions
* Automatic MCQ grading
* Instructor grading for essay questions
* Student performance analytics
* Topic mastery tracking
* AI-generated study plans
* Practice tasks
* AI Tutor / topic explanation
* AI-generated practice quizzes
* Adaptive practice sessions
* Material completion tracking
* Notifications

### Instructor Features

* Instructor registration and authentication
* Instructor dashboard
* Course management
* Module and lesson management
* Learning material uploads
* Automatic text extraction from supported documents
* Video lecture management
* Quiz creation
* Manual question creation
* Essay grading
* Course analytics
* Student performance analytics
* At-risk student identification
* AI-generated course summaries
* AI-generated quizzes
* Study plan review
* Student announcements
* Material management

---

# Agentic AI

The project contains two specialized AI agents with different permissions and responsibilities.

## Student Agent

The Student Agent provides personalized learning assistance.

It can perform actions such as:

* Get student performance
* Analyze topic mastery
* Create personalized study plans
* Create practice tasks
* Recommend quizzes
* Explain topics
* Generate practice quizzes
* Mark practice tasks as completed
* Start adaptive practice sessions
* Submit adaptive practice answers
* End adaptive practice sessions
* Access learning-related information

The Student Agent operates within the authenticated student's context and cannot perform instructor-only actions.

---

## Instructor Agent

The Instructor Agent provides course-management and analytics capabilities.

It can perform actions such as:

* Get course analytics
* Get individual student performance
* Identify at-risk students
* Create quizzes
* Generate course summaries
* Generate quizzes from course materials
* Add questions to quizzes
* Upload learning materials
* Generate summaries from materials
* Grade essay responses
* Review study plans
* Cancel quizzes
* Send course announcements
* Manage instructor-owned course content

Instructor tools are restricted to courses owned by the authenticated instructor.

---

# AI Tutor

The platform includes an **AI Tutor / Explain Topic** feature.

Students can ask the system to explain a topic based on the actual learning materials available in their course.

For supported materials, the system extracts their text and makes it available as context for AI-powered explanations.

This allows the explanation to be grounded in the course content rather than relying only on general model knowledge.

---

# Course Chatbot

The Course Chatbot is intentionally separated from the Agentic AI layer.

It is a **read-only conversational component** designed for course-related questions.

The chatbot does not have access to the agent tools and cannot:

* Create quizzes
* Upload materials
* Modify courses
* Grade students
* Delete data
* Execute instructor actions
* Modify student study plans

This separation helps maintain a clear distinction between conversational assistance and action-taking agents.

---

# AI Architecture

The project uses Google Gemini through the `google-genai` SDK.

The AI architecture includes:

* Centralized Gemini client
* Configurable primary model
* Configurable fallback models
* Rate-limit handling
* Model fallback
* Circuit-breaker behavior
* API error classification
* Offline rule-based NLU
* AI availability diagnostics
* Graceful API failure handling

The centralized AI client is implemented in:

```text
llm_client.py
```

The agents use:

```text
agents/llm_orchestrator.py
```

The application also contains:

```text
agents/nlu.py
```

which provides the offline natural-language routing layer.

---

# Offline Agent Mode

A major design goal of the project is that the core agent workflows do not completely depend on an external LLM API.

The built-in NLU layer can understand supported commands in both **English and Arabic**, including common Egyptian Arabic phrasing.

For example:

```text
Create a study plan

What's my performance?

Explain Django ORM

Quiz me on REST APIs

اعملي خطة مذاكرة

احصائيات الكورس

اشرحلي Django ORM
```

The offline router identifies the requested action, resolves relevant entities such as courses, topics, materials, students, quizzes, and tasks, and then calls the same underlying agent tools used by the LLM path.

This means important application logic remains centralized in the tools rather than being duplicated inside the fallback system.

---

# AI Resilience

The AI integration is designed to handle common external API failures.

### Rate Limits

When a model returns a `429` response, the system can move through the configured model chain instead of repeatedly retrying the same unavailable model.

### Circuit Breaker

Models that enter a cooldown period are temporarily skipped.

If all configured models are unavailable, the system can immediately use the offline fallback instead of repeatedly waiting for failed API requests.

### Invalid or Retired Models

A `404` model error is treated differently from a rate-limit error. Invalid model names can be removed from the active model chain while the system continues with available models.

### Authentication Errors

Authentication and authorization errors such as `401` and `403` are handled separately so that invalid credentials are not repeatedly retried.

### Server Errors

Temporary `5xx` failures can be retried with short backoff intervals.

---

# AI Status

The project provides a Django management command for checking the AI configuration.

```bash
python manage.py ai_status
```

To perform an actual API connectivity test:

```bash
python manage.py ai_status --test
```

The command can be used to inspect the configured AI state, model configuration, and availability.

---

# Offline Mode

For demonstrations, testing, or environments without an API connection, AI requests can be disabled completely.

Set:

```env
AI_OFFLINE_MODE=1
```

The application will then use its built-in rule-based and heuristic mechanisms.

This is especially useful for demonstrations where deterministic behavior is preferred over dependence on API availability or internet connectivity.

---

# Learning Materials

Instructors can upload different types of learning materials:

* PDF
* DOCX
* PPTX
* TXT
* Video

Supported text-based documents are processed using dedicated extraction utilities.

The extracted text can then be used by AI-powered features such as:

* Topic explanations
* Material summaries
* AI-generated quizzes
* Course summaries

Video lectures are stored and played directly in the browser.

The project intentionally does not perform AI transcription or video analysis.

---

# Quiz System

The LMS supports two main question types:

### Multiple Choice Questions

MCQs are automatically graded when the student submits the quiz.

### Essay / Short Answer Questions

Essay responses are stored for instructor review.

The quiz workflow supports:

```text
In Progress
      ↓
Pending Review
      ↓
Graded
```

If a quiz contains essay questions, the MCQ portion can be graded immediately while the remaining score is completed after instructor grading.

---

# AI Quiz Generation

The system can generate quizzes from course materials and topics.

Generated quizzes can contain:

* Multiple Choice Questions
* Essay / Short Answer Questions

The generated quiz can be linked to the material that was used as its source.

The system also contains fallback mechanisms to ensure that quiz generation remains usable when the AI service is unavailable.

---

# Adaptive Practice

The project includes an adaptive practice system designed to maintain server-side practice state.

An adaptive practice session tracks:

* Student
* Topic
* Difficulty
* Correct-answer streak
* Wrong-answer streak
* Number of questions
* Number of correct answers
* Current question
* Current choices
* Active session state

The system can dynamically adjust the practice experience based on the student's ongoing performance.

---

# Study Plans

Students can create personalized study plans through the Student Agent.

A study plan can contain:

* Focus areas
* Practice tasks
* Task priorities
* Recommended quizzes
* Completion status
* Instructor review status

Study plans can be submitted for instructor review and can be approved or rejected.

---

# Analytics

The platform includes analytics for both students and instructors.

### Student Analytics

Students can view information such as:

* Quiz performance
* Score trends
* Topic mastery
* Completed learning materials
* Learning progress

### Instructor Analytics

Instructors can view:

* Course performance
* Student performance
* Score distributions
* Topic mastery
* Student progress
* At-risk students

---

# At-Risk Students

The Instructor Agent includes an **At-Risk Students** capability.

The system analyzes available student performance and topic mastery data to identify students who may require additional attention.

This allows instructors to identify potential learning difficulties and intervene earlier.

---

# Role-Based Access Control

The platform uses role-based access control to separate Student and Instructor capabilities.

| Capability                        | Student | Instructor |
| --------------------------------- | :-----: | :--------: |
| View own performance              |    ✅    |      —     |
| Create own study plan             |    ✅    |      —     |
| Create practice tasks             |    ✅    |      —     |
| Practice with AI                  |    ✅    |      —     |
| Explain course topics             |    ✅    |      —     |
| Take quizzes                      |    ✅    |      —     |
| View course analytics             |    ❌    |      ✅     |
| View student performance          |    ❌    |      ✅     |
| Create quizzes                    |    ❌    |      ✅     |
| Generate course summaries         |    ❌    |      ✅     |
| Upload materials                  |    ❌    |      ✅     |
| Generate summaries from materials |    ❌    |      ✅     |
| Generate quizzes from materials   |    ❌    |      ✅     |
| Add quiz questions                |    ❌    |      ✅     |
| Grade essay responses             |    ❌    |      ✅     |
| Review study plans                |    ❌    |      ✅     |
| Send announcements                |    ❌    |      ✅     |

The agent tools enforce the user's role and resource ownership before executing protected actions.

---

# Agent Action Logging

Agent actions are recorded through the `AgentActionLog` model.

The log records information such as:

* User
* Agent
* Tool name
* Parameters
* Result
* Status
* Timestamp

This provides an audit trail for actions executed through the Agentic AI layer.

---

# Database

The main production-oriented database configuration uses PostgreSQL.

The project also supports SQLite as an optional local development mode.

### PostgreSQL with Docker

Start the database using:

```bash
docker compose up -d
```

The default configuration exposes PostgreSQL on:

```text
localhost:5432
```

Database configuration is controlled through environment variables.

---

# Tech Stack

## Backend

* Python
* Django
* PostgreSQL
* SQLite

## Artificial Intelligence

* Google Gemini
* Google GenAI SDK
* Agentic AI
* Natural Language Understanding
* Rule-based NLU
* AI fallback architecture

## Document Processing

* PyPDF
* python-docx
* python-pptx

## Frontend

* Django Templates
* HTML
* CSS
* JavaScript

## Infrastructure

* Docker
* Docker Compose

---

# Project Structure

```text
lms_project/
│
├── accounts/
│   ├── models.py
│   ├── forms.py
│   ├── views.py
│   └── urls.py
│
├── courses/
│   ├── models.py
│   ├── views.py
│   ├── services.py
│   ├── text_extraction.py
│   ├── ai_generation.py
│   └── management/
│       └── commands/
│           └── seed_demo.py
│
├── planning/
│   ├── models.py
│   ├── views.py
│   └── urls.py
│
├── agents/
│   ├── models.py
│   ├── tools.py
│   ├── student_agent.py
│   ├── instructor_agent.py
│   ├── llm_orchestrator.py
│   ├── nlu.py
│   ├── decorators.py
│   └── management/
│       └── commands/
│           └── ai_status.py
│
├── chatbot/
│   ├── models.py
│   ├── services.py
│   └── views.py
│
├── notifications/
│   ├── models.py
│   ├── services.py
│   └── views.py
│
├── config/
│   ├── settings.py
│   ├── urls.py
│   ├── asgi.py
│   └── wsgi.py
│
├── templates/
├── static/
├── media/
│
├── llm_client.py
├── manage.py
├── requirements.txt
├── docker-compose.yml
├── .env.example
└── .gitignore
```

---

# Installation

## 1. Clone the Repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd lms_project
```

## 2. Create a Virtual Environment

### Windows

```powershell
python -m venv venv
venv\Scripts\activate
```

### Linux / macOS

```bash
python3 -m venv venv
source venv/bin/activate
```

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

## 4. Configure Environment Variables

Create a `.env` file from `.env.example`.

### Windows

```powershell
Copy-Item .env.example .env
```

### Linux / macOS

```bash
cp .env.example .env
```

Configure the required values:

```env
GEMINI_API_KEY=
GEMINI_MODEL=
GEMINI_FALLBACK_MODELS=

AI_OFFLINE_MODE=0

DB_ENGINE=postgresql
DB_NAME=lms_db
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432

DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1
```

A Gemini API key is optional because the project includes offline fallback behavior.

## 5. Start PostgreSQL

```bash
docker compose up -d
```

## 6. Apply Migrations

```bash
python manage.py migrate
```

## 7. Load Demo Data

```bash
python manage.py seed_demo
```

## 8. Check AI Configuration

```bash
python manage.py ai_status
```

Optional API connectivity test:

```bash
python manage.py ai_status --test
```

## 9. Start the Development Server

```bash
python manage.py runserver
```

Open:

```text
http://127.0.0.1:8000/
```

---

# Demo Accounts

The demo seed command creates sample accounts for testing the platform.

| Username      | Password        | Role         |
| ------------- | --------------- | ------------ |
| `instructor1` | `instructor123` | Instructor   |
| `student1`    | `student123`    | Student      |
| `student2`    | `student123`    | Student      |
| `student3`    | `student123`    | Student      |
| `admin`       | `admin123`      | Django Admin |

These credentials are intended only for local demonstration and testing.

For real deployments, replace them with secure credentials.

---

# Testing

The project includes tests for the main application components and Agentic AI behavior.

Run the agent tests with:

```bash
python manage.py test agents
```

The Agentic AI test suite covers areas including:

* Natural-language routing
* English commands
* Arabic commands
* Tool execution
* Permission enforcement
* API error handling
* Rate-limit behavior
* Model fallback
* Circuit-breaker behavior

Run the complete Django test suite with:

```bash
python manage.py test
```

---

# Security Notes

Do not commit sensitive credentials to GitHub.

The project uses:

```text
.env
```

for local environment variables, and `.env` is excluded through `.gitignore`.

Only the example configuration should be committed:

```text
.env.example
```

Never place real API keys, database passwords, or production credentials in the repository.

---

# Current Scope and Limitations

The project is designed primarily as an educational and demonstration LMS.

### Local File Storage

Uploaded materials and videos are stored on the server filesystem.

For production-scale deployments, object storage such as S3-compatible storage and a CDN would be more appropriate.

### Video Processing

Videos are stored and played directly through the browser.

The system does not currently implement:

* Video transcription
* Video summarization
* Adaptive bitrate streaming
* Automatic video conversion

### Offline AI Quality

The offline fallback engine is designed to preserve functionality when Gemini is unavailable.

Its generated content is more limited than the full LLM-powered implementation.

### Course Enrollment

Course enrollment is currently managed through the available instructor/admin workflows and demo data rather than a complete self-service enrollment system.

---

# Project Goal

The goal of this project is to demonstrate how **Agentic AI can be integrated into a Django-based Learning Management System** to provide personalized learning assistance and intelligent course-management workflows.

Instead of using an LLM only as a conversational chatbot, the system connects natural-language requests to application tools that can perform real operations while respecting:

* Authentication
* Role-based access control
* Resource ownership
* Confirmation requirements
* Audit logging
* Application business rules

This architecture combines traditional web application engineering with modern AI agent concepts in a practical educational platform.

---

# License

This project is developed for educational, demonstration, and portfolio purposes.
