"""LangGraph agent 与 graph 定义。

M0 只占位。M4-2 起在这里搭 StateGraph 骨架：
  - fan-out 并行改写节点（每段经历一个实例）
  - fan-in 汇总 + 简历组装
  - Checkpointer（Postgres 后端）
  - 评分循环的条件边回环
"""
