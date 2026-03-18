"""APOLLO — AI Data Intelligence Analyst.

Handles: data analysis questions, report generation, KPI queries,
dashboard requests, trend analysis, business intelligence.
"""

from __future__ import annotations

from app.skills.base import BaseSkill, SkillContext, SkillResult

SYSTEM_PROMPT = """You are APOLLO, an AI Data Intelligence Analyst within the enterprise.
You help employees with data and analytics tasks including:
- Data analysis and interpretation
- Report generation and formatting
- KPI tracking and dashboarding
- Trend identification and anomaly detection
- Business intelligence queries
- SQL query writing and optimization
- Data visualization recommendations
- Predictive analytics guidance
- Cross-departmental data analysis
- Metric definition and tracking

You are speaking to: {user_name} ({user_role})

Rules:
- Be precise with numbers, percentages, and statistical measures
- When analyzing data, state assumptions clearly
- Recommend appropriate visualization types for the data
- If asked to write SQL, include comments explaining the logic
- For predictions, always state confidence levels and limitations
- Protect data privacy — never expose individual-level data unless authorized
- Suggest follow-up analyses when you spot interesting patterns"""


class DataAnalyticsSkill(BaseSkill):
    name = "data_analytics"
    agent_name = "APOLLO"
    description = "Data & analytics: reports, KPIs, SQL, dashboards, trends"
    keywords = [
        "data", "analytics", "analysis", "analyze", "analyse",
        "report", "dashboard", "kpi", "metric", "metrics",
        "sql", "query", "database", "table",
        "trend", "forecast", "predict", "projection",
        "chart", "graph", "visualization", "visualize",
        "average", "median", "percentage", "growth rate",
        "revenue", "conversion", "churn", "retention",
        "bi", "business intelligence", "insight", "insights",
        "anomaly", "outlier", "correlation",
        "excel", "spreadsheet", "csv", "pivot",
    ]
    examples = [
        "What's our monthly revenue trend for the last quarter?",
        "Write a SQL query to find top 10 customers by spend",
        "Help me build a KPI dashboard for the engineering team",
        "Analyze the churn rate data and identify patterns",
        "What visualization should I use for this time-series data?",
        "Create a report template for weekly sales metrics",
    ]

    def can_handle(self, text: str) -> float:
        score = self._keyword_score(text)
        low = text.lower()
        if any(p in low for p in ["analyze data", "write sql", "build dashboard", "kpi report"]):
            score = max(score, 0.85)
        if any(p in low for p in ["trend", "metrics", "analytics", "forecast"]):
            score = max(score, 0.55)
        return score

    async def execute(self, context: SkillContext, request: str) -> SkillResult:
        system = SYSTEM_PROMPT.format(
            user_name=context.user_name,
            user_role=context.user_role or "Analyst",
        )

        response = await self._ask_ollama(system, request)

        return SkillResult(
            success=True,
            message=response,
        )
