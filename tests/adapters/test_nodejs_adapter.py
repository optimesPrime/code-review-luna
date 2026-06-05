import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser
from phases.adapters.nodejs_adapter import NodejsAdapter

ADAPTER = NodejsAdapter()
_parser = Parser(Language(tsts.language_typescript()))


def _parse(src: str):
    source = src.encode("utf-8")
    return _parser.parse(source).root_node, source


EXPRESS_SRC = """\
import express from 'express';
const router = express.Router();

router.post('/orders', async (req, res) => {
  const result = await orderService.submit(req.body);
  res.json(result);
});

router.get('/orders/:id', async (req, res) => {
  const order = await orderService.get(req.params.id);
  res.json(order);
});
"""

NESTJS_SRC = """\
import { Controller, Post, Get, UseGuards, Body } from '@nestjs/common';
import { AuthGuard } from '@nestjs/passport';
import { OrderService } from './order.service';

@Controller('orders')
export class OrderController {
  constructor(private readonly orderService: OrderService) {}

  @UseGuards(AuthGuard('jwt'))
  @Post('submit')
  async submit(@Body() req: SubmitRequest) {
    return this.orderService.submit(req);
  }

  @Get(':id')
  async getOrder(id: string) {
    return this.orderService.get(id);
  }
}
"""

SERVICE_SRC = """\
import { Injectable } from '@nestjs/common';

@Injectable()
export class OrderService {
  async submit(req: SubmitRequest): Promise<OrderResult> {
    await this.prisma.order.create({ data: req });
    return new OrderResult();
  }
}
"""


def test_adapter_name_and_extensions():
    assert ADAPTER.name == "nodejs"
    assert ".ts" in ADAPTER.extensions
    assert ".js" in ADAPTER.extensions


def test_extract_file_nodes_express_route():
    root, src = _parse(EXPRESS_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "routes/orders.ts")
    # At least one node extracted from Express routes
    assert len(nodes) > 0


def test_extract_file_nodes_nestjs_method():
    root, src = _parse(NESTJS_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "controllers/order.controller.ts")
    names = [n.name for n in nodes]
    assert "OrderController.submit" in names


def test_extract_file_nodes_nestjs_controller_type():
    root, src = _parse(NESTJS_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "controllers/order.controller.ts")
    submit = next(n for n in nodes if n.name == "OrderController.submit")
    assert submit.node_type == "controller_action"


def test_extract_file_nodes_nestjs_collects_decorators():
    root, src = _parse(NESTJS_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "controllers/order.controller.ts")
    submit = next(n for n in nodes if n.name == "OrderController.submit")
    assert any("Post" in a or "UseGuards" in a for a in submit.attributes)


def test_extract_file_edges_requires_auth_nestjs():
    root, src = _parse(NESTJS_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "controllers/order.controller.ts")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "controllers/order.controller.ts", method_index)
    assert any(e.edge_type == "requires_auth" for e in edges)


def test_extract_file_edges_writes_db():
    root, src = _parse(SERVICE_SRC)
    nodes = ADAPTER.extract_file_nodes(root, src, "services/order.service.ts")
    method_index = {n.name: n.id for n in nodes}
    edges = ADAPTER.extract_file_edges(root, src, "services/order.service.ts", method_index)
    assert any(e.edge_type == "writes_db" for e in edges)


def test_find_enclosing_symbol_in_nestjs_method():
    root, src = _parse(NESTJS_SRC)
    # Line 12 is inside submit() body
    symbol = ADAPTER.find_enclosing_symbol(root, src, 12, "controllers/order.controller.ts", False)
    assert symbol is not None
    assert symbol.symbol == "submit"
    assert symbol.class_name == "OrderController"
