from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from domain_packs.finance_ashare.tools import snapshot_collector


class _Frame:
    def __init__(self, columns: dict[str, list[object]]) -> None:
        self._columns = dict(columns)

    @property
    def columns(self):
        return self._columns.keys()

    @property
    def empty(self) -> bool:
        return not self._columns

    def copy(self) -> "_Frame":
        return _Frame({key: list(values) for key, values in self._columns.items()})

    def __getitem__(self, key: str):
        return self._columns[key]

    def __setitem__(self, key: str, value) -> None:
        self._columns[key] = value


class UziCapitalFlowProviderCompatTests(unittest.TestCase):
    def test_stock_code_uses_detail_endpoint_and_preserves_uzi_field_contract(self) -> None:
        calls: list[tuple[str, str]] = []

        def broad_loader(*, symbol: str):
            calls.append(("broad", symbol))
            raise AssertionError("a stock code must not trigger the full-market endpoint")

        def detail_loader(*, symbol: str):
            calls.append(("detail", symbol))
            return _Frame({"股东户数-本次": [12345]})

        akshare = SimpleNamespace(
            stock_zh_a_gdhs=broad_loader,
            stock_zh_a_gdhs_detail_em=detail_loader,
        )
        module = SimpleNamespace(ak=akshare)

        class Runtime:
            def collect(self, fetcher_id: str, **kwargs):
                self.assertions(fetcher_id, kwargs)
                frame = module.ak.stock_zh_a_gdhs(symbol=kwargs["ticker"])
                return {"data": {"holder_count": frame["股东户数"][0]}}

            @staticmethod
            def assertions(fetcher_id: str, kwargs: dict[str, object]) -> None:
                if fetcher_id != "12_capital_flow" or kwargs["ticker"] != "688256":
                    raise AssertionError("unexpected UZI invocation")

        with patch.object(snapshot_collector.importlib, "import_module", return_value=module):
            result = snapshot_collector._collect_uzi_fetcher(
                Runtime(),
                fetcher_id="12_capital_flow",
                ticker="688256",
                industry=None,
                dependencies=None,
                options=None,
            )

        self.assertEqual(result["data"]["holder_count"], 12345)
        self.assertEqual(calls, [("detail", "688256")])
        self.assertIs(module.ak, akshare)

    def test_original_provider_is_restored_when_uzi_fetcher_fails(self) -> None:
        def broad_loader(*, symbol: str):
            return _Frame({"股东户数": [1]})

        def detail_loader(*, symbol: str):
            return _Frame({"股东户数-本次": [1]})

        akshare = SimpleNamespace(
            stock_zh_a_gdhs=broad_loader,
            stock_zh_a_gdhs_detail_em=detail_loader,
        )

        class FailingRuntime:
            @staticmethod
            def collect(fetcher_id: str, **kwargs):
                module.ak.stock_zh_a_gdhs(symbol=kwargs["ticker"])
                raise RuntimeError("provider failure")

        module = SimpleNamespace(ak=akshare)
        with (
            patch.object(snapshot_collector.importlib, "import_module", return_value=module),
            self.assertRaisesRegex(RuntimeError, "provider failure"),
        ):
            snapshot_collector._collect_uzi_fetcher(
                FailingRuntime(),
                fetcher_id="12_capital_flow",
                ticker="600519",
                industry=None,
                dependencies=None,
                options=None,
            )

        self.assertIs(module.ak, akshare)


if __name__ == "__main__":
    unittest.main()
