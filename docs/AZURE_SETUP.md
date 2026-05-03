# Azure configuration for local and production

This document lists the Azure and Microsoft identity values you need to run the chatbot **frontend** (Angular), **backend** (Azure Functions in `backend/`), and related services. Replace placeholder text with your real resources.

---

## Quick reference

| Area | Where to configure | Purpose |
|------|-------------------|---------|
| Sign-in (MSAL) | Angular `environment*.ts` + Entra app registration | User login, `user.read` for Microsoft Graph |
| Chat API | Angular `environment*.ts` | POST chat messages to Functions |
| Chat history API | Angular `environment*.ts` | GET/POST history, prime, append, delete |
| Blob storage | Function App settings | Persist gzip JSON per user |
| OpenAI / LangGraph | Function App settings | RAG + LLM (`langgraph_chain.py`) |
| Teams bot (optional) | Function App settings | Bot Framework adapter on `/api/messages` |
| Frontend hosting (prod) | Static Web App URL | Production `redirectUri` |

---

## 1. Microsoft Entra ID (Azure AD) — app registration

Used by the Angular app (`@azure/msal-angular`) and should match your tenant.

### 1.1 Step-by-step: create the app registration (MSAL / Entra client)

Use the [Azure portal](https://portal.azure.com). You need permission to **register applications** in Entra ID (or ask an admin to create the app and share the IDs).

1. **Open Entra ID** — Go to **Microsoft Entra ID**.
2. **New registration** — **App registrations** → **New registration**.
3. **Basics**
   - **Name:** e.g. `Chatbot SPA`.
   - **Supported account types:** usually **Accounts in this organizational directory only** for a single-tenant line-of-business app. Use multitenancy only if you need it.
   - **Redirect URI:** leave empty here; add SPA URIs in the next step. Click **Register**.
4. **Redirect URIs (Authentication)** — Open **Authentication** → **Add a platform** → **Single-page application**. Add each URL that will load your Angular app (must match **exactly** — scheme, host, port, path; typically **no** trailing slash):
   - Local: `http://localhost:4200` (and optionally `http://127.0.0.1:4200`).
   - Production: e.g. `https://your-app.azurestaticapps.net` or your custom domain.
   - For modern MSAL (authorization code + PKCE), you usually **do not** enable **Implicit grant** tokens unless something in your stack still requires it.
5. **API permissions** — **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions** → add **`User.Read`** → **Add permissions**. Grant **admin consent** for the tenant if required.
6. **Copy IDs** — On **Overview**, copy **Application (client) ID** → `msalClientId` and **Directory (tenant) ID** → `msalTenantId` in `environment.ts` / `environment.prod.ts`. Set **`redirectUri`** to the same value as one of the registered SPA URIs.
7. **No browser secret** — This SPA is a public client; **do not** embed a client secret in Angular. Optional **Certificates & secrets** are for confidential server apps, not the MSAL browser flow used here.
8. **Common errors** — If redirect URIs don’t match, sign-in fails (often `AADSTS50011`). After deploying production, register that URL and update `environment.prod.ts`.

Official docs: [Register an app](https://learn.microsoft.com/entra/identity-platform/quickstart-register-app), [Redirect URI restrictions](https://learn.microsoft.com/entra/identity-platform/reply-url).

### 1.2 Quick reference

| Value | Description | Local typical value | Production typical value |
|-------|-------------|---------------------|---------------------------|
| **Application (client) ID** | Entra app registration → *Overview* | Same app, same ID | Same |
| **Directory (tenant) ID** | Entra → *Overview* | Your tenant GUID | Same |
| **Redirect URI (SPA)** | *Authentication* → Single-page application | `http://localhost:4200` | `https://<your-static-web-app>.azurestaticapps.net` (or your custom domain) |
| **API permissions** | *Microsoft Graph* → delegated **`User.Read`** (matches `msal.config.ts`) | — | — |

**Frontend environment fields:**

| Variable in `environment.ts` / `environment.prod.ts` | Maps to |
|------------------------------------------------------|---------|
| `msalClientId` | Application (client) ID |
| `msalTenantId` | Directory (tenant) ID |
| `redirectUri` | Must exactly match a registered SPA redirect URI |

**Authority** (built in code): `https://login.microsoftonline.com/{msalTenantId}`

---

## 2. Angular environments (frontend)

Files: `src/environments/environment.ts` (local), `src/environments/environment.prod.ts` (production build).

| Key | Description | Example (local) | Example (production) |
|-----|-------------|-----------------|----------------------|
| `production` | Angular build flag | `false` | `true` |
| `msalClientId` | Entra client ID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` | same |
| `msalTenantId` | Entra tenant ID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` | same |
| `redirectUri` | SPA URL | `http://localhost:4200` | `https://<app>.azurestaticapps.net` |
| `chatApiUrl` | Full URL to chat HTTP trigger | `http://localhost:7071/api/nuvoco_frontend` *or* deployed function URL | `https://<function-app>.azurewebsites.net/api/nuvoco_frontend` |
| `chatHistoryApiUrl` | **Base** API URL (no trailing slash); app appends `/chat_history`, etc. | `http://localhost:7071/api` | `https://<function-app>.azurewebsites.net/api` |

**Important:** `chatHistoryApiUrl` must be the prefix that ends with `/api`, because the code builds paths like `${chatHistoryApiUrl}/chat_history`.

---

## 3. Azure Functions (Python backend)

Configure these in the **Function App → Configuration → Application settings** (production) or in `backend/local.settings.json` under `Values` (local).

### 3.1 Required for chat history (blob storage)

| Setting | Required | Description |
|---------|----------|---------------|
| `AZURE_STORAGE_CONNECTION_STRING` | **Yes** | Storage account connection string (Blob). Used by `chat_history.py`. |
| `CHAT_HISTORY_CONTAINER` | No | Blob container name. Default in code: **`chat-history`**. |

Blob layout (per user): `{container}/{sanitized_user_id}/chat-history.json.gz`  
The frontend sends the signed-in user’s **email** (`username`) as `user_id`; paths must match that string (after sanitization).

### 3.2 Optional — Microsoft Teams / Bot Framework

Only if you use `POST /api/messages` with the Bot Framework adapter (`function_app.py`).

| Setting | Description |
|---------|-------------|
| `MICROSOFT_APP_ID` | Bot’s Microsoft App ID |
| `MICROSOFT_APP_PASSWORD` | Bot client secret |
| `MICROSOFT_APP_TENANT_ID` | Tenant used for bot auth |

If the Teams bot package is not installed, the messages route can return 503.

### 3.3 LangGraph / LLM (`langgraph_chain.py`)

These appear in the repo’s Python code:

| Setting | Required | Default / notes |
|---------|----------|----------------|
| `AZURE_OPENAI_MODEL` | For chat flow | Azure OpenAI **deployment name** passed to `llm.chat.completions.create` |
| `MAX_TURNS` | No | Default **`10`** (maps to message window sizing) |

Your deployed project may also use a separate `llm` module (not always in source control) that expects standard Azure OpenAI variables. **Verify** your `llm.py` implementation; commonly you will also set:

| Setting | Typical use |
|---------|-------------|
| `AZURE_OPENAI_ENDPOINT` | `https://<resource>.openai.azure.com/` |
| `AZURE_OPENAI_API_KEY` | Key for the OpenAI resource |
| `AZURE_OPENAI_API_VERSION` | e.g. `2024-02-15-preview` |

RAG/search modules (e.g. `search_documents.py`) may introduce **additional** keys (search service name, index, API key). Add those in the Function App when that code is present.

---

## 4. Local development — `local.settings.json`

Copy `backend/local.settings.json.example` to `backend/local.settings.json` (gitignored) and fill in values.

The Function host loads this file automatically when debugging locally.

### Python worker concurrency

Chat history uses **async** Azure Blob I/O (`azure.storage.blob.aio`), so many concurrent HTTP requests do not block each other on storage network I/O. LangGraph / LLM work runs in a **thread pool** (`asyncio.to_thread`) so the async event loop stays responsive.

Example shape:

```json
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "AZURE_STORAGE_CONNECTION_STRING": "<your-storage-connection-string>",
    "CHAT_HISTORY_CONTAINER": "chat-history",
    "AZURE_OPENAI_MODEL": "<your-deployment-name>",
    "MAX_TURNS": "10",
    "MICROSOFT_APP_ID": "",
    "MICROSOFT_APP_PASSWORD": "",
    "MICROSOFT_APP_TENANT_ID": ""
  },
  "Host": {
    "LocalHttpPort": 7071
  }
}
```

Point the Angular `environment.ts` URLs at `http://localhost:7071/api` (or your chosen port).

---

## 5. Production — Function App

1. Deploy the contents of `backend/` to a **Python** Azure Function App (same routes as in `function_app.py`: `ping`, `messages`, `nuvoco_frontend`, `chat_history`, `chat_history_delete`, `prime_conversation`, `append_exchange`).
2. Set all **Application settings** from section 3.
3. `host.json` uses HTTP route prefix **`api`** (unless you change it), so public URLs are `https://<function-app>.azurewebsites.net/api/...`.

---

## 6. Production — Static Web App (or other frontend host)

If you host the Angular build on **Azure Static Web Apps**:

| Item | Action |
|------|--------|
| Deploy URL | e.g. `https://<name>.azurestaticapps.net` |
| `environment.prod.ts` | Set `redirectUri` and API base URLs to this hostname and your Function App URL |
| Entra app | Add the Static Web App URL as an SPA **redirect URI** |

`staticwebapp.config.json` is for SPA routing and headers; it does not store secrets.

---

## 7. CORS (if frontend and Functions are on different origins)

If the browser calls the Function App directly from `localhost:4200` or from a Static Web App domain, enable **CORS** on the Function App for those origins (or put an API gateway / SWA **linked** API in front—depends on your architecture).

---

## 8. Checklist before go-live

- [ ] Entra SPA redirect URIs include **local** and **production** frontend URLs.  
- [ ] `chatApiUrl` and `chatHistoryApiUrl` point to the deployed Function App `/api` routes.  
- [ ] `AZURE_STORAGE_CONNECTION_STRING` is set; container exists (or app can create it).  
- [ ] `AZURE_OPENAI_MODEL` (and any extra keys required by `llm.py`) are set.  
- [ ] Same **user identifier** (email from MSAL) is used consistently so blob paths match stored data.  
- [ ] Optional: Teams bot secrets, if using `/api/messages`.

---

## 9. Files in this repo that consume these values

| File | Consumes |
|------|----------|
| `src/environments/environment.ts` | MSAL + API base URLs (local) |
| `src/environments/environment.prod.ts` | MSAL + API base URLs (prod) |
| `src/app/config/msal.config.ts` | Builds MSAL config from `environment` |
| `backend/chat_history.py` | `AZURE_STORAGE_CONNECTION_STRING`, `CHAT_HISTORY_CONTAINER` |
| `backend/function_app.py` | Bot env vars; routes wire chat + history |
| `backend/langgraph_chain.py` | `AZURE_OPENAI_MODEL`, `MAX_TURNS` |

---

*Generated from the repository layout. Adjust any missing `llm`/search modules to match your actual deployment.*
