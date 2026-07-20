from __future__ import annotations

import json
import os
import sys
from typing import Any

import pymysql
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

from core.workflow.error_sanitizer import sanitized_error_payload
from core.workflow.tool_contract import build_tool_payload, dump_tool_payload


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


# -----------------------------------------------------------------------------
# Environment
# -----------------------------------------------------------------------------
dotenv_path = os.path.join(AGENT_DIR, ".env")
load_dotenv(dotenv_path)
try:
    from config.secrets import load_file_secrets

    load_file_secrets()
except Exception:
    pass


mcp = FastMCP("CloudPlatformMCPServer")


def get_db_connection():
    """Get a remote MySQL connection."""
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "YOUR_MYSQL_HOST"),
        port=int(os.getenv("MYSQL_PORT", 3306)),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "YOUR_MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE", "cloud_platform"),
        cursorclass=pymysql.cursors.DictCursor,
    )


PRODUCT_CATALOG: dict[str, dict[str, Any]] = {
    "P_ECS_G8A_XLARGE": {
        "name": "ECS 第八代通用型实例 ecs.g8a.xlarge",
        "keywords": ["ecs", "云服务器", "通用型", "g8a", "4核6g", "amd", "genoa"],
        "price": 299.0,
    },
    "P_ECS_C7_8XLARGE": {
        "name": "ECS 第七代计算型实例 ecs.c7.8xlarge",
        "keywords": ["ecs", "云服务器", "计算型", "c7", "32核64g", "高并发", "intel"],
        "price": 1299.0,
    },
    "P_GPU_GN7I": {
        "name": "GPU 计算型实例 ecs.gn7i-c8g1.2xlarge",
        "keywords": ["gpu", "算力", "大模型", "a10", "深度学习", "推理", "gn7i"],
        "price": 3500.0,
    },
    "P_RDS_MYSQL_HA": {
        "name": "云数据库 RDS MySQL 高可用版",
        "keywords": ["rds", "mysql", "数据库", "关系型", "高可用", "主备", "同城容灾"],
        "price": 599.0,
    },
    "P_ESSD_PL1": {
        "name": "ESSD PL1 性能云盘",
        "keywords": ["云盘", "块存储", "essd", "pl1", "存储"],
        "price": 50.0,
    },
}


PROMOTIONS: dict[str, dict[str, str]] = {
    "P_ECS_G8A_XLARGE": {
        "title": "ECS 第八代通用型 (g8a.xlarge) 开发者特惠",
        "desc": "基于 AMD EPYC 9004 处理器，4核6G。最高网络带宽 10Gbps，首年立减 8.5 折。",
        "base_link": "https://promotion.cloud.com/ecs-g8a-special",
        "commission_rate": "15%",
    },
    "P_ECS_C7_8XLARGE": {
        "title": "ECS 第七代计算型 (c7.8xlarge) 大促",
        "desc": "32核64G，最高网络带宽 10Gbps，适合高并发 Web 应用，购买包年套餐赠 ESSD PL1 云盘 100G。",
        "base_link": "https://promotion.cloud.com/ecs-c7-high-concurrency",
        "commission_rate": "18%",
    },
    "P_GPU_GN7I": {
        "title": "GPU 算力特惠 (gn7i-c8g1.2xlarge)",
        "desc": "搭载 1 块 NVIDIA A10 GPU (24GB 显存)，适合深度学习推理与 AIGC 设计。",
        "base_link": "https://promotion.cloud.com/gpu-a10-aigc",
        "commission_rate": "25%",
    },
    "P_RDS_MYSQL_HA": {
        "title": "RDS MySQL 高可用版 同城双活首选",
        "desc": "一主一备双节点架构，支持 30 秒内自动故障迁移，提供高可用读写分离能力。",
        "base_link": "https://promotion.cloud.com/rds-mysql-ha",
        "commission_rate": "12%",
    },
    "P_ALL_000": {
        "title": "云上全家桶 满减活动",
        "desc": "全场云产品满 1000 减 100，适合还在观望的用户快速上手。",
        "base_link": "https://promotion.cloud.com/all-in-one",
        "commission_rate": "10%",
    },
}


def _promotable_products() -> list[dict[str, Any]]:
    return [
        {
            "product_id": pid,
            "product_name": info["name"],
            "price": info["price"],
        }
        for pid, info in PRODUCT_CATALOG.items()
        if pid != "P_ESSD_PL1"
    ]


def _search_products(keyword: str) -> list[dict[str, Any]]:
    kw_lower = keyword.strip().lower()
    results = []
    for pid, info in PRODUCT_CATALOG.items():
        if kw_lower in info["name"].lower() or any(kw_lower in str(item).lower() for item in info["keywords"]):
            results.append(
                {
                    "product_id": pid,
                    "product_name": info["name"],
                    "price": info["price"],
                }
            )
    return results


def _resolve_product_id_by_name(product_name: str) -> tuple[str, bool]:
    product_lower = product_name.strip().lower()
    if "gpu" in product_lower or "算力" in product_name or "大模型" in product_name:
        return "P_GPU_GN7I", True
    if "rds" in product_lower or "mysql" in product_lower:
        return "P_RDS_MYSQL_HA", True
    if "c7" in product_lower or "高并发" in product_name or "计算型" in product_name:
        return "P_ECS_C7_8XLARGE", True
    if "ecs" in product_lower or "云服务器" in product_name or "服务器" in product_name:
        return "P_ECS_G8A_XLARGE", True
    if "云盘" in product_name or "存储" in product_name or "essd" in product_lower:
        return "P_ALL_000", False
    return "P_ALL_000", False


def _promotion_materials_payload(product_id: str, user_id: str = "", *, matched: bool = True) -> dict[str, Any]:
    promo_key = product_id if product_id in PROMOTIONS else "P_ALL_000"
    promo = PROMOTIONS[promo_key]
    status = "success" if matched and product_id in PROMOTIONS else "not_found"
    exclusive_link = f"{promo['base_link']}?inviter={user_id}&pid={product_id}" if user_id else promo["base_link"]
    user_message = (
        f"已获取 {product_id} 的推广物料。"
        if status == "success"
        else f"未识别产品 {product_id}，已返回通用推广物料。"
    )
    error_code = "" if status == "success" else "UNKNOWN_PRODUCT"
    return build_tool_payload(
        status,
        data={
            "product_id": product_id,
            "activity_title": promo["title"],
            "selling_points": promo["desc"],
            "exclusive_link": exclusive_link,
            "commission_rate": promo["commission_rate"],
        },
        user_message=user_message,
        error_code=error_code,
    )


def _promotion_materials_payload_by_name(product_name: str, user_id: str = "") -> dict[str, Any]:
    product_id, matched = _resolve_product_id_by_name(product_name)
    return _promotion_materials_payload(product_id, user_id, matched=matched)


@mcp.tool()
def get_promotable_products() -> str:
    promotable_list = _promotable_products()
    return dump_tool_payload(
        "success",
        data=promotable_list,
        user_message=f"已找到 {len(promotable_list)} 个可推广商品。",
    )


@mcp.tool()
def search_product_catalog(keyword: str) -> str:
    results = _search_products(keyword)
    if not results:
        return dump_tool_payload(
            "not_found",
            data={
                "matches": [],
                "recommendation": {
                    "product_id": "P_ALL_000",
                    "product_name": "全场通用云产品活动",
                },
            },
            user_message=f"未找到精确匹配 '{keyword}' 的产品，已返回通用推荐。",
            error_code="NO_MATCH",
        )

    return dump_tool_payload(
        "success",
        data=results,
        user_message=f"已找到 {len(results)} 个匹配商品。",
    )


@mcp.tool()
def get_promotion_materials(product_id: str, user_id: str = "") -> str:
    return _json(_promotion_materials_payload(product_id, user_id))


@mcp.tool()
def generate_ai_poster(prompt: str) -> str:
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        return dump_tool_payload(
            "error",
            user_message="未配置 DASHSCOPE_API_KEY，无法生成海报。",
            error_code="CONFIG_MISSING",
            error_type_value="CONFIG_MISSING",
        )

    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": "qwen-image-2.0",
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ]
        },
        "parameters": {
            "negative_prompt": "低分辨率，低画质，肢体畸形，文字模糊，构图混乱",
            "prompt_extend": True,
            "watermark": False,
            "size": "1536*2688",
        },
    }

    last_status = "POSTER_GENERATION_FAILED"
    for attempt in range(1, 3):
        try:
            sys.stderr.write(f"[AI-POSTER][QWEN] attempt={attempt} submit start\n")
            response = requests.post(url, json=payload, headers=headers, timeout=120)
            data = response.json()
            request_id = data.get("request_id", "")
            sys.stderr.write(
                f"[AI-POSTER][QWEN] attempt={attempt} status={response.status_code} request_id={request_id}\n"
            )

            image_url = (
                data.get("output", {})
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", [{}])[0]
                .get("image")
            )
            if response.status_code == 200 and image_url:
                sys.stderr.write(f"[AI-POSTER][QWEN] attempt={attempt} success\n")
                return dump_tool_payload(
                    "success",
                    data={
                        "poster_url": image_url,
                        "request_id": request_id,
                    },
                    user_message="海报生成成功。",
                )

            last_status = str(data.get("code") or response.status_code or last_status)
            sys.stderr.write(f"[AI-POSTER][QWEN] attempt={attempt} failed: {last_status}\n")
        except Exception as exc:
            last_status = exc.__class__.__name__
            sys.stderr.write(f"[AI-POSTER][QWEN] attempt={attempt} exception_type={last_status}\n")

    return dump_tool_payload(
        "error",
        user_message="海报生成失败，请稍后重试。",
        error_code="POSTER_GENERATION_FAILED",
        error_type_value="POSTER_GENERATION_FAILED",
    )


@mcp.tool()
def query_user_orders(user_id: str, limit: int = 5) -> str:
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            sql = """
                SELECT order_id, product_name, billing_mode, amount, status, DATE_FORMAT(created_at, '%%Y-%%m-%%d %%H:%%i:%%s') as created_at
                FROM cloud_orders
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT %s
            """
            cursor.execute(sql, (user_id, limit))
            results = cursor.fetchall()

            for row in results:
                if "amount" in row and row["amount"] is not None:
                    row["amount"] = float(row["amount"])

            return dump_tool_payload(
                "success",
                data=results,
                user_message=(
                    f"已查询到 {len(results)} 条订单记录。"
                    if results
                    else "未查询到该用户的订单记录。"
                ),
            )
    except Exception as exc:
        return _json(sanitized_error_payload("查询数据库", exc))
    finally:
        if "connection" in locals() and connection.open:
            connection.close()


@mcp.tool()
def query_user_instances(user_id: str, limit: int = 5) -> str:
    sql = """
        SELECT instance_id, instance_type, region_id, zone_id, public_ip, status
        FROM cloud_instances
        WHERE user_id = %s
        ORDER BY instance_id DESC
        LIMIT %s
    """

    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(sql, (user_id, limit))
            results = cursor.fetchall()

            return dump_tool_payload(
                "success",
                data=results,
                user_message=(
                    f"已查询到 {len(results)} 条实例记录。"
                    if results
                    else "未查询到该用户的实例记录。"
                ),
            )
    except Exception as exc:
        return _json(sanitized_error_payload("查询数据库", exc))
    finally:
        if "connection" in locals() and connection.open:
            connection.close()


@mcp.tool()
def analyze_instance_usage(instance_id: str, user_id: str = "") -> str:
    if not instance_id:
        return dump_tool_payload(
            "error",
            user_message="必须提供实例 ID (instance_id)。",
            error_code="INVALID_ARGUMENT",
            error_type_value="INVALID_ARGUMENT",
        )

    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            auth_sql = """
                SELECT instance_id
                FROM cloud_instances
                WHERE instance_id = %s AND user_id = %s
                LIMIT 1
            """
            cursor.execute(auth_sql, (instance_id, user_id))
            owned_instance = cursor.fetchone()
            if not owned_instance:
                return dump_tool_payload(
                    "error",
                    user_message="未找到该实例或无权查看监控数据。",
                    error_code="FORBIDDEN",
                    error_type_value="FORBIDDEN",
                )

            metrics_sql = """
                SELECT
                    ROUND(AVG(avg_cpu_usage_percent), 2) AS cpu_usage_percent,
                    ROUND(AVG(avg_memory_usage_percent), 2) AS memory_usage_percent,
                    ROUND(MAX(max_network_out_mbps), 2) AS network_out_bandwidth_mbps,
                    COUNT(*) AS days_count
                FROM instance_metrics_daily
                WHERE instance_id = %s
                  AND user_id = %s
                  AND metric_date >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
            """
            cursor.execute(metrics_sql, (instance_id, user_id))
            agg = cursor.fetchone()

            if not agg or not agg.get("days_count"):
                return dump_tool_payload(
                    "error",
                    user_message="未查询到该实例近 7 天监控数据，请稍后再试。",
                    error_code="NO_METRICS",
                    error_type_value="NO_METRICS",
                )

            cpu = float(agg["cpu_usage_percent"] or 0)
            memory = float(agg["memory_usage_percent"] or 0)
            bandwidth = float(agg["network_out_bandwidth_mbps"] or 0)

            if cpu < 10 and memory < 30:
                diagnosis = "RESOURCES_IDLE"
            elif cpu > 70 or memory > 80:
                diagnosis = "RESOURCES_TIGHT"
            else:
                diagnosis = "RESOURCES_NORMAL"

            return dump_tool_payload(
                "success",
                data={
                    "instance_id": instance_id,
                    "owner_id": user_id,
                    "metrics_7d_avg": {
                        "cpu_usage_percent": cpu,
                        "memory_usage_percent": memory,
                        "network_out_bandwidth_mbps": bandwidth,
                    },
                    "diagnosis": diagnosis,
                },
                user_message="已完成实例监控分析。",
            )
    except Exception as exc:
        return _json(sanitized_error_payload("查询监控数据", exc))
    finally:
        if "connection" in locals() and connection.open:
            connection.close()


def get_promotion_materials_by_name(product_name: str, user_id: str = "") -> str:
    return _json(_promotion_materials_payload_by_name(product_name, user_id))


if __name__ == "__main__":
    sys.stderr.write("[MCP] starting Cloud Platform MCP server (stdio)\n")
    mcp.run()
