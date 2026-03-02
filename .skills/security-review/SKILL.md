---
name: security-review
description: Use when implementing authentication, authorization, data validation, external API calls, file operations, database queries, or any security-sensitive code - before marking work complete
---

# Security Review

## Overview

Security bugs are asymmetric: they're cheap to prevent and catastrophic to fix after exploitation.

**Core principle:** ALWAYS apply security review to sensitive code. One missed check is one breach.

**Violating the letter of this process is violating the spirit of security.**

## The Iron Law

```
NO SECURITY-SENSITIVE CODE WITHOUT EXPLICIT SECURITY REVIEW
```

If it touches auth, data, external input, or files — review it.

## Trigger: When to Run This Skill

**MANDATORY for any code that:**
- Handles authentication or authorization
- Processes user-supplied input
- Makes database queries
- Calls external APIs or services
- Reads or writes files
- Handles secrets, tokens, or credentials
- Manages user sessions
- Processes financial or sensitive data

**ESPECIALLY when:**
- Under time pressure ("quick fix" mindset)
- Refactoring security-adjacent code
- Adding new API endpoints
- Changing access control logic

## Phase 1: Attack Surface Mapping

Before reviewing logic, map what can be attacked.

**For each component, ask:**
1. What input does this accept? From whom?
2. What does it output? To whom?
3. What resources does it access?
4. What decisions does it make?

```
Input sources: HTTP params, headers, body, env vars, files, DB reads
Output sinks: HTTP response, files, DB writes, external API calls
Resources: DB, filesystem, network, memory, CPU
Decisions: Auth checks, permission gates, data visibility
```

## Phase 2: OWASP Top 10 Checklist

Work through each category for the code under review:

**A01: Broken Access Control**
- [ ] Every sensitive endpoint checks permissions
- [ ] No client-controlled access (role in JWT body, not just header)
- [ ] No IDOR (user can only access their own data)
- [ ] Directory traversal impossible in file paths
- [ ] No forced browsing to restricted resources

**A02: Cryptographic Failures**
- [ ] No plaintext storage of sensitive data
- [ ] Passwords use bcrypt/argon2 (NOT md5/sha1/sha256)
- [ ] Secrets not hardcoded or in source control
- [ ] TLS enforced for data in transit
- [ ] Random tokens use cryptographically secure RNG

**A03: Injection**
- [ ] All DB queries use parameterized queries / ORM (NO string concatenation)
- [ ] All shell commands sanitize input (NO subprocess with user input)
- [ ] HTML output escapes user content (prevent XSS)
- [ ] LDAP/XML/JSON injection prevented

**A04: Insecure Design**
- [ ] Sensitive operations rate-limited
- [ ] Brute force protection on auth endpoints
- [ ] No sensitive data in URLs (logs capture URLs)
- [ ] Error messages don't leak system info

**A05: Security Misconfiguration**
- [ ] No debug mode in production
- [ ] Default credentials changed
- [ ] Unnecessary features disabled
- [ ] Security headers set (CSP, HSTS, X-Frame-Options)

**A07: Authentication Failures**
- [ ] Session invalidated on logout
- [ ] Tokens expire (no eternal sessions)
- [ ] Password reset uses secure tokens (not predictable)
- [ ] Multi-factor considered for sensitive operations

**A08: Software and Data Integrity**
- [ ] Dependencies checked for known vulnerabilities
- [ ] No deserialization of untrusted data
- [ ] File uploads validated (type, size, content)

**A09: Logging and Monitoring**
- [ ] Auth failures logged
- [ ] No secrets logged (passwords, tokens, PII)
- [ ] Log injection prevented (newlines in user input)

## Phase 3: Common Attack Patterns

Check for each pattern specifically:

### SQL Injection
```python
# VULNERABLE
query = f"SELECT * FROM users WHERE id = {user_id}"

# SAFE
query = "SELECT * FROM users WHERE id = ?"
cursor.execute(query, (user_id,))
```

### Path Traversal
```python
# VULNERABLE
filepath = os.path.join(base_dir, user_input)

# SAFE
filepath = os.path.join(base_dir, user_input)
if not filepath.startswith(os.path.abspath(base_dir)):
    raise ValueError("Path traversal detected")
```

### Mass Assignment
```python
# VULNERABLE
user.update(**request.json())

# SAFE
allowed_fields = {"name", "email"}
user.update(**{k: v for k, v in request.json().items() if k in allowed_fields})
```

### Timing Attacks
```python
# VULNERABLE (timing attack possible)
if token == stored_token:

# SAFE
import hmac
if hmac.compare_digest(token, stored_token):
```

## Phase 4: Severity Classification

After identifying issues, classify each:

**CRITICAL (Block deployment):**
- Auth bypass
- SQL/command injection
- Hardcoded secrets
- Data exposure to unauthorized users
- Session hijacking vectors

**HIGH (Fix before release):**
- Missing rate limiting on auth
- Sensitive data in logs
- Weak cryptography (md5, sha1 for passwords)
- Missing CSRF protection
- Improper session management

**MEDIUM (Fix in next sprint):**
- Missing security headers
- Verbose error messages
- Overly permissive CORS
- Missing input validation (non-injection)

**LOW (Backlog):**
- Security improvements without active risk
- Defense in depth improvements

## Phase 5: Report Format

```
## Security Review: [Component Name]

### Attack Surface
- Input: [What external input this handles]
- Output: [What it produces/exposes]
- Decisions: [What access decisions it makes]

### Findings

#### Critical
1. **[Vulnerability Type]**
   - File: path/to/file.py:line
   - Issue: [What the vulnerability is]
   - Attack: [How it could be exploited]
   - Fix: [Specific code-level fix]

#### High
[Same format]

#### Medium / Low
[Same format]

### Cleared
[What was explicitly checked and found secure]

### Verdict
Secure for deployment: [Yes / No / With fixes]
```

## Red Flags - STOP and Review

If you see any of these, apply this skill immediately:
- String concatenation in SQL queries
- `eval()` or `exec()` with user input
- `subprocess` with user-controlled strings
- File paths constructed from user input without validation
- Tokens/passwords compared with `==`
- Passwords stored without hashing
- `pickle.loads()` on external data
- Auth checks that can be skipped
- Error messages that include stack traces

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "Internal API, not exposed" | Internal APIs get exposed. Assume it will be. |
| "Input is already validated" | Where? By what? Check the actual validation. |
| "Just a prototype" | Prototypes become production. Build it right. |
| "No sensitive data here" | Are you sure? Check logs, errors, responses. |
| "Security team will review later" | Security bugs multiply when ignored. Fix now. |
| "It passed the tests" | Tests don't test for security by default. |

## Final Rule

```
Security-sensitive code → Security review → Fix findings → Deploy
```

One missed injection point is one breach. Review everything in scope.
