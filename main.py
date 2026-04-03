"""
智能聊天插件 - Smart Chat Plugin
功能：主动找人聊天，活跃群聊氛围
"""
import asyncio
import random
from datetime import datetime, timedelta
from typing import Dict, List, Set, Optional
import json
import os

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, EventMessageType
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import *

@register(
    "smart_chat", 
    "智能聊天插件", 
    "一款能够主动找人聊天、活跃群聊氛围的智能插件。如果群内长时间无人说话，插件会自动选择成员进行对话。", 
    "1.0.0"
)
class SmartChatPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        
        # 配置参数
        self.config = {
            "idle_threshold_seconds": 300,  # 空闲阈值(秒)
            "check_interval_seconds": 60,    # 检查间隔(秒)
            "chat_probability": 0.3,         # 主动聊天概率 (0.0-1.0)
            "max_chats_per_check": 2,        # 每次最多聊天人数
            "min_message_interval": 10,      # 消息最小间隔(秒)
            "cooldown_per_user": 300,        # 用户冷却时间(秒)
            "ai_enabled": False,             # 是否使用AI生成消息
            "fallback_messages": [           # 备用消息列表
                "有人吗~",
                "好无聊啊，有人聊聊吗？",
                "大家都在忙什么呀？",
                "来个人陪我聊聊天呗~",
                "这个群好安静啊",
                "有人想玩个游戏吗？",
                "今天天气怎么样呀？",
                "有人推荐好看的电影吗？",
                "你们喜欢吃什么呢？",
                "周末大家都做什么呀？",
                "有什么有趣的事情分享一下吗？",
            ],
        }
        
        # 运行时状态
        self.group_last_activity: Dict[str, datetime] = {}
        self.group_members: Dict[str, Set[int]] = {}
        self.last_chatted_users: Dict[str, Dict[int, datetime]] = {}
        self.bot_last_sent_time: Dict[str, datetime] = {}
        
        # 加载配置
        self._load_config()
        
        # 定时任务句柄
        self._check_task: Optional[asyncio.Task] = None
        
        logger.info("智能聊天插件已加载!")

    def _load_config(self):
        """从配置文件加载配置"""
        try:
            config_path = os.path.join(os.path.dirname(__file__), "smart_chat_config.json")
            if os.path.exists(config_path):
                with open(config_path, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    self.config.update(loaded_config)
                    logger.info("已加载智能聊天插件配置")
        except Exception as e:
            logger.warning(f"加载配置文件失败，使用默认配置: {e}")

    def _save_config(self):
        """保存配置到文件"""
        try:
            config_path = os.path.join(os.path.dirname(__file__), "smart_chat_config.json")
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")

    async def initialize(self):
        """插件初始化"""
        logger.info("智能聊天插件初始化中...")
        self._check_task = asyncio.create_task(self._idle_check_loop())
        logger.info("智能聊天插件初始化完成!")

    async def terminate(self):
        """插件卸载/停用"""
        logger.info("智能聊天插件正在卸载...")
        if self._check_task:
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        logger.info("智能聊天插件已卸载")

    async def _idle_check_loop(self):
        """定时检查群聊是否空闲"""
        while True:
            try:
                await asyncio.sleep(self.config["check_interval_seconds"])
                await self._check_all_groups()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"检查群聊空闲状态时出错: {e}")
                await asyncio.sleep(60)

    async def _check_all_groups(self):
        """检查所有群组是否需要主动聊天"""
        try:
            all_groups = await self._get_all_groups()
            for group_id in all_groups:
                try:
                    await self._maybe_start_chat(group_id)
                except Exception as e:
                    logger.error(f"检查群 {group_id} 时出错: {e}")
        except Exception as e:
            logger.error(f"获取群组列表失败: {e}")

    async def _get_all_groups(self) -> List[str]:
        """获取所有加入的群组"""
        try:
            if hasattr(self.context, 'get_groups'):
                groups = await self.context.get_groups()
                return [str(g.get('group_id', g.get('id', g))) for g in groups]
            return []
        except Exception as e:
            logger.debug(f"获取群组列表: {e}")
            return []

    async def _maybe_start_chat(self, group_id: str):
        """检查并决定是否主动聊天"""
        now = datetime.now()
        
        # 检查是否距离上次发送消息时间太近
        last_sent = self.bot_last_sent_time.get(group_id)
        if last_sent:
            time_since_last_sent = (now - last_sent).total_seconds()
            if time_since_last_sent < self.config["min_message_interval"]:
                return
        
        # 检查是否超过空闲阈值
        last_activity = self.group_last_activity.get(group_id)
        if not last_activity:
            self.group_last_activity[group_id] = now
            return
        
        idle_time = (now - last_activity).total_seconds()
        
        if idle_time >= self.config["idle_threshold_seconds"]:
            if random.random() < self.config["chat_probability"]:
                await self._initiate_conversation(group_id)

    async def _initiate_conversation(self, group_id: str):
        """主动发起对话"""
        logger.info(f"群 {group_id} 空闲时间过长，开始主动聊天...")
        
        members = await self._get_group_members(group_id)
        if not members:
            logger.warning(f"群 {group_id} 成员列表为空")
            return
        
        available_members = self._filter_available_members(group_id, members)
        if not available_members:
            logger.info(f"群 {group_id} 没有可聊天的成员")
            return
        
        num_to_chat = min(
            random.randint(1, self.config["max_chats_per_check"]),
            len(available_members)
        )
        selected_members = random.sample(available_members, num_to_chat)
        
        for member_id in selected_members:
            await self._send_proactive_message(group_id, member_id)
            await asyncio.sleep(random.randint(3, 8))

    async def _get_group_members(self, group_id: str) -> List[int]:
        """获取群成员列表"""
        try:
            if group_id in self.group_members:
                return list(self.group_members[group_id])
            
            if hasattr(self.context, 'get_group_members'):
                members = await self.context.get_group_members(group_id)
                member_ids = [int(m.get('user_id', m.get('id', m))) for m in members]
                self.group_members[group_id] = set(member_ids)
                return member_ids
            
            return []
        except Exception as e:
            logger.debug(f"获取群成员列表失败: {e}")
            return []

    def _filter_available_members(self, group_id: str, members: List[int]) -> List[int]:
        """过滤出可聊天的成员"""
        now = datetime.now()
        available = []
        
        if group_id not in self.last_chatted_users:
            self.last_chatted_users[group_id] = {}
        
        for member_id in members:
            last_chatted = self.last_chatted_users[group_id].get(member_id)
            if last_chatted:
                time_since = (now - last_chatted).total_seconds()
                if time_since < self.config["cooldown_per_user"]:
                    continue
            
            available.append(member_id)
        
        return available

    async def _send_proactive_message(self, group_id: str, target_user_id: int):
        """发送主动消息"""
        now = datetime.now()
        
        message = random.choice(self.config["fallback_messages"])
        
        try:
            await self._send_group_message(group_id, message)
            
            self.bot_last_sent_time[group_id] = now
            self.last_chatted_users[group_id][target_user_id] = now
            
            logger.info(f"向群 {group_id} 发送消息: {message}")
        except Exception as e:
            logger.error(f"发送消息失败: {e}")

    async def _send_group_message(self, group_id: str, message: str):
        """发送群消息"""
        try:
            if hasattr(self.context, 'send_group_message'):
                await self.context.send_group_message(group_id, [Plain(message)])
            elif hasattr(self.context, 'send_message'):
                await self.context.send_message(group_id, [Plain(message)])
            else:
                logger.warning("发送消息API不可用")
        except Exception as e:
            logger.error(f"发送群消息失败: {e}")
            raise

    # ==================== 事件处理 ====================
    
    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent):
        """处理群消息事件"""
        try:
            group_id = str(event.get_group_id())
            user_id = event.get_sender_id()
            
            # 更新群组活跃时间
            self.group_last_activity[group_id] = datetime.now()
            
            # 更新用户活跃时间
            if group_id not in self.last_chatted_users:
                self.last_chatted_users[group_id] = {}
            self.last_chatted_users[group_id][user_id] = datetime.now()
        except Exception as e:
            logger.error(f"处理群消息事件出错: {e}")

    # ==================== 指令处理 ====================
    
    @filter.command("smartchat")
    async def cmd_smartchat(self, event: AstrMessageEvent):
        """查看智能聊天插件状态"""
        group_id = str(event.get_group_id())
        
        status_lines = [
            "=== 智能聊天插件状态 ===",
            f"空闲阈值: {self.config['idle_threshold_seconds']} 秒",
            f"检查间隔: {self.config['check_interval_seconds']} 秒",
            f"主动聊天概率: {self.config['chat_probability'] * 100:.0f}%",
            f"AI模式: {'开启' if self.config['ai_enabled'] else '关闭'}",
        ]
        
        if group_id in self.group_last_activity:
            last_act = self.group_last_activity[group_id]
            idle = (datetime.now() - last_act).total_seconds()
            status_lines.append(f"当前群空闲时间: {int(idle)} 秒")
        
        yield event.plain_result("\n".join(status_lines))

    @filter.command("活跃")
    async def cmd_activate_chat(self, event: AstrMessageEvent):
        """强制激活聊天"""
        group_id = str(event.get_group_id())
        
        self.group_last_activity[group_id] = datetime.now() - timedelta(
            seconds=self.config["idle_threshold_seconds"] + 1
        )
        
        yield event.plain_result("好的！我来活跃一下气氛~")

    @filter.command("设置空闲时间")
    async def cmd_set_idle_time(self, event: AstrMessageEvent):
        """设置空闲时间阈值"""
        try:
            args = event.message_str.replace("设置空闲时间", "").strip()
            seconds = int(args)
            
            if seconds < 30:
                yield event.plain_result("空闲时间不能少于30秒哦~")
                return
            
            self.config["idle_threshold_seconds"] = seconds
            self._save_config()
            
            yield event.plain_result(f"已设置空闲时间为 {seconds} 秒")
        except ValueError:
            yield event.plain_result("请输入有效的秒数，例如：设置空闲时间 300")

    @filter.command("设置聊天概率")
    async def cmd_set_probability(self, event: AstrMessageEvent):
        """设置主动聊天概率"""
        try:
            args = event.message_str.replace("设置聊天概率", "").strip()
            probability = float(args)
            
            if probability < 0 or probability > 1:
                yield event.plain_result("概率值需要在 0 到 1 之间，例如：0.3 表示30%")
                return
            
            self.config["chat_probability"] = probability
            self._save_config()
            
            yield event.plain_result(f"已设置主动聊天概率为 {probability * 100:.0f}%")
        except ValueError:
            yield event.plain_result("请输入有效的概率值，例如：0.3")

    @filter.command("toggleai")
    async def cmd_toggle_ai(self, event: AstrMessageEvent):
        """切换AI模式"""
        self.config["ai_enabled"] = not self.config["ai_enabled"]
        self._save_config()
        
        status = "开启" if self.config["ai_enabled"] else "关闭"
        yield event.plain_result(f"AI模式已{status}")

    @filter.command("help")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """
=== 智能聊天插件使用指南 ===

【指令】
/smartchat - 查看插件状态
/活跃 - 强制激活聊天
/设置空闲时间 [秒] - 设置空闲阈值
/设置聊天概率 [0-1] - 设置主动聊天概率
/toggleai - 切换AI模式
/help - 显示此帮助

【功能说明】
- 当群内超过设定时间无人说话时，会主动找人聊天
- 可配置聊天概率和空闲阈值
- 支持对每个用户设置冷却时间

【配置文件】
smart_chat_config.json 可详细配置各项参数
"""
        yield event.plain_result(help_text.strip())
