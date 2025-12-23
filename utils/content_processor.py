"""内容处理器模块 - 针对不同平台的内容格式化"""

import re
from abc import ABC, abstractmethod
from typing import Dict, Optional
from bs4 import BeautifulSoup
import html

from astrbot.api import logger


class ContentProcessor(ABC):
    """内容处理器基类"""
    
    @abstractmethod
    def process(self, item: dict, config: dict) -> dict:
        """处理RSS条目内容
        
        Args:
            item: 原始RSS条目数据
            config: 配置信息
            
        Returns:
            处理后的数据，包含:
            - clean_description: 清理后的描述文本
            - video_url: 视频链接(如果有)
            - extra_links: 其他链接(如图文地址等)
            - display_title: 显示标题(可能与原标题不同)
        """
        pass
    
    @abstractmethod
    def match(self, url: str) -> bool:
        """判断此处理器是否适用于该URL
        
        Args:
            url: RSS订阅URL
            
        Returns:
            是否匹配
        """
        pass


class BilibiliProcessor(ContentProcessor):
    """B站内容处理器"""
    
    def match(self, url: str) -> bool:
        """匹配B站RSS链接"""
        return 'bilibili' in url.lower()
    
    def process(self, item: dict, config: dict) -> dict:
        """处理B站RSS内容
        
        B站RSS的description格式:
        格式1 (视频动态):
            标题内容<br>-<br><img src="封面图"><br>视频地址：<a href="视频链接">...</a>
        
        格式2 (图文动态):
            标题内容<br>分享图片<br><img src="图片"><br>图文地址：<a href="图文链接">...</a>
        
        格式3 (纯文本动态):
            标题内容<br>其他内容...
        """
        description = item.get('description', '').strip()
        title = item.get('title', '').strip()
        
        result = {
            'clean_description': '',
            'video_url': '',
            'extra_links': {},
            'display_title': title,
        }
        
        if not description:
            return result
        
        # 使用BeautifulSoup解析HTML
        soup = BeautifulSoup(description, 'html.parser')
        
        # 1. 提取视频链接
        video_links = soup.find_all('a', href=re.compile(r'bilibili\.com/video'))
        if video_links:
            result['video_url'] = video_links[0].get('href', '')
        else:
            # 尝试从纯文本中提取
            video_match = re.search(r'https://www\.bilibili\.com/video/[A-Za-z0-9]+', description)
            if video_match:
                result['video_url'] = video_match.group(0)
        
        # 2. 提取图文链接
        opus_links = soup.find_all('a', href=re.compile(r'bilibili\.com/opus'))
        if opus_links:
            result['extra_links']['opus'] = opus_links[0].get('href', '')


        
        # 3. 处理纯文本（description已经被parser转换为纯文本）
        text = description
        
        logger.info(f"[B站处理器] 原始description: {repr(text[:300])}")
        
        # 3.1 格式化各类链接地址（保留"XX地址："前缀，添加emoji和换行）
        # 格式：嗯嗯 - 视频地址： https://... → 嗯嗯\n🎬 视频地址：https://...
        text = re.sub(r'\s*-\s*视频地址[：:]\s*', '\n🎬 视频地址：', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*-\s*图文地址[：:]\s*', '\n📄 图文地址：', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*-\s*直播间地址[：:]\s*', '\n🎙️ 直播间地址：', text, flags=re.IGNORECASE)
        # 也处理没有破折号的情况
        text = re.sub(r'\s*视频地址[：:]\s*', '\n🎬 视频地址：', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*图文地址[：:]\s*', '\n📄 图文地址：', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*直播间地址[：:]\s*', '\n🎙️ 直播间地址：', text, flags=re.IGNORECASE)
        
        logger.info(f"[B站处理器] 处理后text: {repr(text[:300])}")
        
        # 4. 分行处理
        lines = text.split('\n')
        cleaned_lines = []
        
        for line in lines:
            line = line.strip()
            
            # 跳过空行
            if not line:
                continue
            
            # 跳过单独的破折号
            if line in ['-', '—', '–', '－']:
                continue
            
            # 跳过"分享图片"
            if line == '分享图片':
                continue
            
            cleaned_lines.append(line)
        
        # 5. 组合描述文本
        if cleaned_lines:
            clean_desc = '\n'.join(cleaned_lines)
            
            # 移除开头和结尾的引号
            clean_desc = re.sub(r'^["\'"]+|["\'"]+$', '', clean_desc).strip()
            
            # 截断处理
            max_len = config.get('push', {}).get('max_description_length', 200)
            if len(clean_desc) > max_len:
                # 找到截断位置附近的换行符，避免截断到一行中间
                cut_pos = clean_desc.rfind('\n', 0, max_len)
                if cut_pos > max_len * 0.8:
                    clean_desc = clean_desc[:cut_pos] + '\n...'
                else:
                    clean_desc = clean_desc[:max_len] + '...'
            
            # 如果描述太短，不显示
            if len(clean_desc) >= 2:
                result['clean_description'] = clean_desc
        
        logger.debug(f"B站内容处理结果: {result}")
        return result


class DefaultProcessor(ContentProcessor):
    """默认内容处理器 - 用于未知平台"""
    
    def match(self, url: str) -> bool:
        """总是返回True,作为兜底处理器"""
        return True
    
    def process(self, item: dict, config: dict) -> dict:
        """默认处理: 简单清理HTML标签"""
        description = item.get('description', '').strip()
        title = item.get('title', '').strip()
        
        result = {
            'clean_description': '',
            'video_url': '',
            'extra_links': {},
            'display_title': title,
        }
        
        if not description:
            return result
        
        # 移除HTML标签
        soup = BeautifulSoup(description, 'html.parser')
        clean_text = soup.get_text(separator=' ', strip=True)
        
        # 解码HTML实体
        clean_text = html.unescape(clean_text)
        
        # 清理空白
        clean_text = re.sub(r'\s+', ' ', clean_text).strip()
        
        # 移除引号
        clean_text = re.sub(r'^["\'"]+|["\'"]+$', '', clean_text).strip()
        
        # 截断
        max_len = config.get('push', {}).get('max_description_length', 200)
        if len(clean_text) > max_len:
            clean_text = clean_text[:max_len] + '...'
        
        if len(clean_text) >= 2:
            result['clean_description'] = clean_text
        
        return result


class ContentProcessorFactory:
    """内容处理器工厂"""
    
    def __init__(self):
        # 注册所有处理器(顺序很重要,DefaultProcessor必须最后)
        self.processors = [
            BilibiliProcessor(),
            # 未来可以在这里添加更多处理器:
            # TwitterProcessor(),
            # WeiboProcessor(),
            # YouTubeProcessor(),
            DefaultProcessor(),  # 必须放在最后作为兜底
        ]
    
    def get_processor(self, url: str) -> ContentProcessor:
        """根据URL获取合适的处理器
        
        Args:
            url: RSS订阅URL
            
        Returns:
            匹配的处理器实例
        """
        for processor in self.processors:
            if processor.match(url):
                logger.debug(f"使用处理器: {processor.__class__.__name__}")
                return processor
        
        # 理论上不会到这里,因为DefaultProcessor总是匹配
        return DefaultProcessor()
