# Orchestration Plan

Generated 2026-09-04T10:46:33+00:00

## Selected specialists (27 of 28)

- **AI Engineer** (`ai_engineer`) — brief shows 'ai' signal
- **Architect** (`architect`) — core to any hackathon submission
- **Backend Engineer** (`backend_engineer`) — brief shows 'backend' signal
- **Brand Designer** (`brand_designer`) — naming and pitch identity are in scope
- **Code Reviewer** (`code_reviewer`) — there will be non-trivial code
- **Competitor Researcher** (`competitor_researcher`) — brief is judged partly on market/business reasoning
- **Demo Engineer** (`demo_engineer`) — core to any hackathon submission
- **Developer** (`developer`) — generalist for wiring and fix tasks
- **DevOps Engineer** (`devops_engineer`) — brief is judged on a judge being able to run it
- **Final Auditor** (`final_auditor`) — core to any hackathon submission
- **Frontend Engineer** (`frontend_engineer`) — coherence: a UX/UI spec was commissioned, so someone must build it
- **Market Researcher** (`market_researcher`) — brief is judged partly on market/business reasoning
- **ML Engineer** (`ml_engineer`) — brief shows 'ml' signal
- **Pitch Strategist** (`pitch_strategist`) — core to any hackathon submission
- **Presentation Builder** (`presentation_builder`) — core to any hackathon submission
- **Product Manager** (`product_manager`) — core to any hackathon submission
- **Requirements Analyst** (`requirements_analyst`) — core to any hackathon submission
- **Requirements Auditor** (`requirements_auditor`) — core to any hackathon submission
- **Security Reviewer** (`security_reviewer`) — there will be code and possibly credentials
- **Product Strategist** (`strategist`) — core to any hackathon submission
- **Submission Manager** (`submission_manager`) — core to any hackathon submission
- **Technical Researcher** (`technical_researcher`) — establishes the technical path and the user
- **Technical Writer** (`technical_writer`) — core to any hackathon submission
- **Tester / QA Engineer** (`tester`) — core to any hackathon submission
- **UI Designer** (`ui_designer`) — brief shows 'design' signal
- **User Researcher** (`user_researcher`) — establishes the technical path and the user
- **UX Designer** (`ux_designer`) — brief shows 'design' signal

## Deliberately not activated

- `database_engineer` — not required by this brief

## Task graph

```
○ requirements [requirements_analyst] critical
  ○ product_plan [product_manager] critical
    ○ architecture [architect] critical
      ○ test [tester] critical
        ○ demo [demo_engineer] critical
          ○ final_audit [final_auditor] critical
            ○ submission [submission_manager] critical
        ○ req_audit [requirements_auditor] high
          ○ final_audit [final_auditor] critical
      ○ ai [ai_engineer] critical
      ○ backend [backend_engineer] critical
      ○ ml [ml_engineer] critical
      ○ docs [technical_writer] high
        ○ final_audit [final_auditor] critical
      ○ security [security_reviewer] high
      ○ integrate [developer] high
      ○ frontend [frontend_engineer] high
      ○ code_review [code_reviewer] medium
      ○ devops [devops_engineer] medium
    ○ ux [ux_designer] high
      ○ ui [ui_designer] medium
  ○ architecture [architect] critical
  ○ strategy [strategist] high
    ○ pitch [pitch_strategist] high
      ○ slides [presentation_builder] high
        ○ final_audit [final_auditor] critical
    ○ brand [brand_designer] low
○ user_research [user_researcher] high
○ tech_research [technical_researcher] high
○ competition [competitor_researcher] medium
○ market [market_researcher] medium
```
