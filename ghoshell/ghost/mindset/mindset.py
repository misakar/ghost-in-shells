from __future__ import annotations

from abc import ABCMeta, abstractmethod
from typing import Optional, Iterator

from ghoshell.ghost.exceptions import MindsetNotFoundException
from ghoshell.ghost.mindset.think import Think, ThinkDriver, ThinkMeta


class Mindset(metaclass=ABCMeta):
    """
    定义了 Ghost 拥有的思维方式
    核心是可以通过 UniformReactionLocator 取出 Reaction
    """

    @abstractmethod
    def fetch(self, thinking: str) -> Optional[Think]:
        """
        获取一个 Thinking
        """
        pass

    @abstractmethod
    def fetch_meta(self, thinking: str) -> Optional[ThinkMeta]:
        """
        获取一个 Thinking的 Meta, 如果存在的话.
        """
        pass

    def force_fetch(self, thinking: str) -> Think:
        """
        随手放一个语法糖方便自己.
        """
        fetched = self.fetch(thinking)
        if fetched is None:
            raise MindsetNotFoundException("todo message")
        return fetched

    @abstractmethod
    def register_sub_mindset(self, mindset: Mindset) -> None:
        """
        注册子级 mindset
        父级里查不到, 就到 sub mindset 里查
        这样的话, 就可以实现 mindset 的继承和重写.
        Clone 可以因此拥有和 Ghost 不同的 Mindset
        """
        pass

    @abstractmethod
    def register_driver(self, driver: ThinkDriver) -> None:
        """
        注册 think 的驱动.
        """
        pass

    @abstractmethod
    def register_meta(self, meta: ThinkMeta) -> None:
        """
        注册一个 thinking
        当然, Mindset 可以有自己的实现, 从某个配置体系中获取.
        或者合并多个 Mindset.
        """
        pass

    def register_think(self, think: Think) -> None:
        """
        用现成的 Think 完成注册.
        """
        meta = think.to_meta()
        self.register_meta(meta)
        if isinstance(think, ThinkDriver):
            self.register_driver(think)

    @abstractmethod
    def foreach_think(self) -> Iterator[Think]:
        """
        需要提供一种机制, 遍历所有的 Think 对象.
        """
        pass