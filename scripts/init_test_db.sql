-- 测试库预建脚本（幂等）：新环境一条命令建库并授权
-- 用法：mysql -u root -p < scripts/init_test_db.sql
CREATE DATABASE IF NOT EXISTS ai_cooker_test
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'ai_cooker'@'localhost' IDENTIFIED BY 'ai_cooker';
CREATE USER IF NOT EXISTS 'ai_cooker'@'127.0.0.1' IDENTIFIED BY 'ai_cooker';
GRANT ALL PRIVILEGES ON ai_cooker_test.* TO 'ai_cooker'@'localhost';
GRANT ALL PRIVILEGES ON ai_cooker_test.* TO 'ai_cooker'@'127.0.0.1';
FLUSH PRIVILEGES;
