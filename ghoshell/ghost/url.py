from typing import Dict

from pydantic import BaseModel, Field


class UniformResolverLocator(BaseModel):
    """
    思维座标, 用类似 URL 的方式来定义.
    """

    # 对应的 Resolver 名, 对标 url 的 path. 用来标记一个 Resolver, 本质上是一个有限状态机.
    resolver: str

    # Resolver 的一个状态，对标 url 的 fragment。
    stage: str = ""

    # 参数, 如果是需要入参的状态机, 不传入正确的参数可能会报错, 或者影响运转的初始状态.
    # 注意, 每个 Resolver 能力应该有自己的 arguments 协议.
    args: Dict = Field(default_factory=lambda: {})

    # def is_same(self, url: "UniformMindLocator") -> bool:
    #     return (self.ghost == url.ghost or url.ghost == "") and self.Resolver == url.Resolver
    @classmethod
    def new(cls, resolver: str, stage: str, args: Dict):
        return URL(resolver=resolver, stage=stage, args=args)

    def to_stage(self, stage: str) -> "UniformResolverLocator":
        return UniformResolverLocator(
            resolver=self.resolver,
            stage=stage,
            args=self.args.copy()
        )

    def new_args(self, args: Dict) -> "UniformResolverLocator":
        return UniformResolverLocator(
            resolver=self.resolver,
            stage=self.stage,
            args=args.copy()
        )

    # def is_same(self, other: "url") -> bool:
    #     return (other.ghost == "" or self.ghost == "" or self.ghost == other.ghost) \
    #         and self.Resolver == other.Resolver \
    #         and self.stage == other.stage


URL = UniformResolverLocator
