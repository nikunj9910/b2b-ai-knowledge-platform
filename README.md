# B2B AI Knowledge Management Platform

> An AI-powered B2B knowledge management platform that enables organizations and teams to securely upload, process, analyze, search, and interact with their audio, video, and document-based knowledge.

![Architecture](https://img.shields.io/badge/Architecture-RAG%20%7C%20REST%20%7C%20RBAC-blue)
![Frontend](https://img.shields.io/badge/Frontend-Next.js%20%7C%20React-black)
![Backend](https://img.shields.io/badge/Backend-FastAPI%20%7C%20Python-green)
![Database](https://img.shields.io/badge/Database-Supabase%20%7C%20PostgreSQL-orange)
![AI](https://img.shields.io/badge/AI-RAG%20%7C%20LLM-purple)

---

## 🚀 Overview

Organizations generate large amounts of knowledge through lectures, meetings, presentations, documents, and recorded sessions. Finding and understanding this information manually can be time-consuming.

This platform converts that unstructured content into an **AI-powered searchable knowledge base**.

Users can:

* Create organizations and teams
* Upload audio, video, and documents
* Automatically transcribe and extract content
* Generate AI-powered summaries and analysis
* Ask questions about uploaded content using RAG
* Translate generated content
* Share knowledge with teams
* Control access using role-based permissions
* Export processed knowledge in multiple formats

The system combines **FastAPI, Next.js, Supabase, vector search, transcription, embeddings, and LLM-powered analysis** into a single knowledge platform.

---

# 🧠 Core Architecture

```text
                         USER
                           │
                           ▼
                    Next.js Frontend
                           │
                           ▼
                      FastAPI API
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
   Organizations       Lectures          AI Services
   & Teams             & Files           & RAG
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                        Supabase
                ┌──────────┼──────────┐
                │          │          │
             PostgreSQL  Storage   pgvector
                │
                ▼
        Structured Knowledge
```

# 🔄 Content Processing Pipeline

```text
Audio / Video / Document
          │
          ▼
   Supabase Storage
          │
          ▼
 Transcription / Extraction
          │
          ▼
 Structured Transcript
          │
          ▼
    AI Analysis
          │
          ▼
     Text Chunking
          │
          ▼
    Cohere Embeddings
          │
          ▼
     Vector Storage
          │
          ▼
      User Query
          │
          ▼
   Similarity Retrieval
          │
          ▼
      Relevant Chunks
          │
          ▼
          LLM
          │
          ▼
   Context-Based Answer
```

---

# 🤖 AI & RAG Capabilities

## Retrieval-Augmented Generation

The platform uses RAG to answer questions using the user's uploaded knowledge rather than relying only on the LLM's general knowledge.

```text
User Question
      ↓
Question Embedding
      ↓
Vector Similarity Search
      ↓
Relevant Document Chunks
      ↓
Context Construction
      ↓
LLM
      ↓
Grounded Answer
```

## RAG Pipeline

* Transcript/document chunking
* Cohere embeddings
* pgvector storage
* Similarity-based retrieval
* Context construction
* LLM-generated answers

---

# 🎙️ Audio & Video Processing

Audio and video content can be transformed into structured transcripts.

```text
Audio / Video
     ↓
Deepgram Nova-2
     ↓
Speaker Detection
     ↓
Word Timestamps
     ↓
Utterances
     ↓
Language Detection
     ↓
Structured Transcript
```

The structured transcript can contain:

* Speakers
* Utterances
* Timestamps
* Duration
* Word count
* Detected language

---

# 📄 Document Processing

The platform supports document-based knowledge extraction.

```text
PDF / DOCX / PPTX
       ↓
Text Extraction
       ↓
Structured Content
       ↓
Chunking
       ↓
Embeddings
       ↓
Vector Search
```

---

# ✨ AI Features

The platform provides multiple AI-powered operations:

| Feature     | Purpose                                |
| ----------- | -------------------------------------- |
| Summary     | Generate concise content summaries     |
| Notes       | Generate structured notes              |
| Keywords    | Extract important keywords             |
| Questions   | Generate questions from content        |
| Topics      | Identify major topics                  |
| Highlights  | Identify important sections            |
| Translation | Translate generated content            |
| RAG Chat    | Ask questions about uploaded knowledge |

---

# 🏢 Organization & Team Management

The system is designed around an organization/workspace model.

```text
Organization
│
├── Owner
│
├── Admins
│
├── Members
│
└── Teams
    │
    ├── Team Admin
    └── Team Members
```

## Organization Roles

* Owner
* Admin
* Member

## Team Roles

* Admin
* Member

This allows organizations to control who can access shared knowledge.

---

# 🔐 Role-Based Access Control

The platform implements access control around organizations, teams, and lectures.

Content can have different scopes:

```text
Personal Lecture
       ↓
Only uploader can access
```

```text
Workspace Lecture
       ↓
Organization members can access
```

```text
Team Lecture
       ↓
Team members + organization admins/owner
```

A centralized permission check is used to protect operations such as:

* Lecture listing
* Lecture access
* Chat
* AI analysis
* Content operations

---

# 💡 AI Team Suggestions

The platform also includes a team suggestion system that can recommend relevant teams when assigning content.

The recommendation logic can consider factors such as:

```text
Existing Team Membership
        +
Content Similarity
        +
Common Team Members
        +
Team Context
        ↓
Team Recommendation Score
```

The recommendation score is bounded between **0 and 100** to maintain a predictable scoring range.

---

# 📤 Export

Processed knowledge can be exported in multiple formats:

* PDF
* Markdown
* TXT
* JSON

This allows generated knowledge and analysis to be reused outside the platform.

---

# 🏗️ Backend Architecture

```text
backend/
└── app/
    │
    ├── routers/
    │   ├── auth.py
    │   ├── organizations.py
    │   ├── groups.py
    │   ├── lectures.py
    │   ├── chat.py
    │   ├── analysis.py
    │   └── export.py
    │
    └── services/
        ├── transcription_service.py
        ├── document_extraction_service.py
        ├── rag_service.py
        ├── analysis_service.py
        ├── organization_service.py
        ├── group_service.py
        └── supabase_client.py
```

## Backend Responsibilities

### FastAPI

Provides REST APIs for authentication, organizations, teams, lectures, analysis, chat, and exports.

### Transcription Service

Processes audio/video through Deepgram.

### Document Extraction Service

Extracts text from supported documents.

### RAG Service

Handles chunking, embeddings, retrieval, and context-based answers.

### Analysis Service

Provides AI-powered summaries, notes, keywords, questions, topics, highlights, and translations.

### Organization & Group Services

Manage workspace and team membership.

---

# 💻 Frontend Architecture

```text
frontend/
└── src/
    ├── app/
    │   └── (protected)/
    │       ├── organizations/
    │       ├── groups/
    │       ├── workspace-view/
    │       └── lecture/
    │
    └── lib/
        └── api.ts
```

## Main Interfaces

### Organizations

Create and manage workspaces.

### Groups

Create and manage teams.

### Workspace

View and upload organization/team content.

### Lecture Details

Access:

* Transcripts
* AI analysis
* RAG chat
* Translation
* Flashcards
* Exports

---

# 🔌 API Overview

## Authentication

```text
POST /api/auth/register
POST /api/auth/login
GET  /api/auth/me
```

## Organizations

```text
POST   /api/organizations
GET    /api/organizations
GET    /api/organizations/{id}/members
GET    /api/organizations/{id}/role
POST   /api/organizations/{id}/invite
DELETE /api/organizations/{id}/members/{user_id}
DELETE /api/organizations/{id}
```

## Teams

```text
POST   /api/groups
GET    /api/groups/org/{org_id}
GET    /api/groups/{id}
GET    /api/groups/{id}/members
POST   /api/groups/{id}/members
DELETE /api/groups/{id}/members/{user_id}
```

## Lectures

```text
POST   /api/lectures/upload
GET    /api/lectures
GET    /api/lectures/{id}
DELETE /api/lectures/{id}
```

## AI Analysis

```text
POST /api/analysis/summary
POST /api/analysis/notes
POST /api/analysis/keywords
POST /api/analysis/questions
POST /api/analysis/topics
POST /api/analysis/highlights
POST /api/analysis/translate
```

## RAG Chat

```text
POST /api/lectures/{id}/chat
```

## Export

```text
POST /api/export/pdf
POST /api/export/markdown
POST /api/export/txt
POST /api/export/json
```

---

# 🗄️ Database Design

```text
organizations
      │
      ├── org_members
      │
      └── groups
            │
            └── group_members


organizations
      │
      └── lectures
             │
             ├── lecture_chunks
             │
             └── lecture_analysis
```

## Important Tables

| Table              | Purpose                                     |
| ------------------ | ------------------------------------------- |
| `organizations`    | Workspace information                       |
| `org_members`      | Organization users and roles                |
| `groups`           | Teams                                       |
| `group_members`    | Team membership                             |
| `lectures`         | Uploaded content and transcript information |
| `lecture_chunks`   | RAG chunks and embeddings                   |
| `lecture_analysis` | Cached AI analysis                          |

---

# 🛠️ Technology Stack

## Frontend

* Next.js
* React
* TypeScript
* Axios
* CSS

## Backend

* Python
* FastAPI

## Database

* Supabase PostgreSQL
* pgvector

## Storage

* Supabase Storage

## AI & Processing

* **Deepgram Nova-2** — transcription
* **Cohere** — embeddings
* **Groq** — AI analysis
* **RAG** — content-based question answering

## Authentication

* JWT

## Architecture

* REST APIs
* Retrieval-Augmented Generation
* Role-Based Access Control
* Vector Search

---

# 📌 Key Engineering Concepts Demonstrated

This project demonstrates practical experience with:

* Agentic/AI-powered application architecture
* Retrieval-Augmented Generation
* Vector databases
* Embeddings
* LLM integration
* Speech-to-text pipelines
* Document processing
* REST API design
* Role-Based Access Control
* Multi-tenant organization architecture
* Team-based permissions
* AI-powered recommendations
* PostgreSQL
* pgvector
* Frontend/backend integration
* Knowledge management systems

---

# 🔮 Future Improvements

Potential future enhancements include:

* Improved team recommendation ranking
* Semantic team matching
* Knowledge graph integration
* Advanced hybrid search
* More granular permissions
* Usage analytics
* Improved retrieval evaluation
* Personalized knowledge recommendations

---

# 👨‍💻 Project Focus

This project focuses on building an **AI-native knowledge platform** rather than a traditional CRUD application.

The core idea is:

```text
Unstructured Organizational Knowledge
                ↓
      Transcription / Extraction
                ↓
          AI Processing
                ↓
        Structured Knowledge
                ↓
       Embeddings + Retrieval
                ↓
          RAG / AI Chat
                ↓
       Actionable Information
```

---

# ⭐ Why This Project?

The platform demonstrates how modern AI systems can be integrated with conventional software engineering concepts such as:

**Authentication + RBAC + REST APIs + PostgreSQL + Storage + Vector Search + LLMs + RAG**

to create a complete end-to-end AI application.
