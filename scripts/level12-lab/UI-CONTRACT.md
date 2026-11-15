UI-MAP.json is a flat object of CSS selectors with these exact keys:
login_username, login_password, login_submit, logout,
nav_dashboard, nav_customers, nav_tasks, nav_articles, menu_toggle,
customer_name, customer_email, customer_stage, customer_submit, customer_search,
article_title, article_body, article_submit.
Do not hide create forms behind extra buttons: visible in the respective view.
Each submit is actual API mutation and lists show saved data. Set query= as
explicit URLSearchParams on customer search requests for deterministic delayed
reply test. GET /api/customers returns {customers:[...]}, /api/tasks {tasks:[...]},
/api/articles {articles:[...]}, dashboard returns counts directly. Lists support
edit/delete, tasks support create/status/delete, articles admin publish/delete,
customer CSV export and confirmation before delete. Browser protected transport
performs real workflow + delayed-reply assertions and screenshots. Cannot edit it.
