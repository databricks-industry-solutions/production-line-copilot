-- Grants for Line Manager Agent Server on Lakebase Autoscaling (OAuth principal).
--
-- Replace every <DATABRICKS_CLIENT_ID> with your app UUID (same as DATABRICKS_CLIENT_ID).
-- Run in Lakebase SQL Editor as a user allowed to GRANT on these schemas.
--
-- Tutorial grants only cover schema ``public``. This app uses ``ai_serving`` (SSE /
-- publisher) and ``ai_chatbot`` (conversations). Without USAGE + object privileges you get:
-- ``permission denied for schema ai_serving``.

CREATE SCHEMA IF NOT EXISTS ai_serving;
CREATE SCHEMA IF NOT EXISTS ai_chatbot;

GRANT USAGE, CREATE ON SCHEMA ai_serving TO "<DATABRICKS_CLIENT_ID>";
GRANT USAGE, CREATE ON SCHEMA ai_chatbot TO "<DATABRICKS_CLIENT_ID>";

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA ai_serving TO "<DATABRICKS_CLIENT_ID>";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA ai_serving TO "<DATABRICKS_CLIENT_ID>";

GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA ai_chatbot TO "<DATABRICKS_CLIENT_ID>";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA ai_chatbot TO "<DATABRICKS_CLIENT_ID>";
