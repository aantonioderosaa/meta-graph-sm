import { describe, expect, it } from "vitest";
import { filterEntitaElements, isKernelCategoriaNode } from "./layout-entita";
import type { EventGraphElements } from "./types";

describe("layout-entita", () => {
  const mockElements: EventGraphElements = {
    nodes: [
      // Nodi categoria
      {
        data: {
          id: "categoria1",
          label: "Categoria 1",
          tipo: "KernelCategoria",
          ordinale: 0,
        },
      },
      {
        data: {
          id: "categoria2",
          label: "Categoria 2",
          tipo: "KernelCategoria",
          ordinale: 1,
        },
      },
      // Nodi menzione
      {
        data: {
          id: "menzione1",
          label: "Menzione 1",
          tipo: "Menzione",
          parent: "categoria1",
        },
      },
      {
        data: {
          id: "menzione2",
          label: "Menzione 2",
          tipo: "Menzione",
          parent: "categoria2",
        },
      },
      // Nodi non correlati
      {
        data: {
          id: "evento1",
          label: "Evento 1",
          tipo: "Evento",
        },
      },
    ],
    edges: [
      // Arco tra categorie
      {
        data: {
          id: "arco1",
          source: "categoria1",
          target: "categoria2",
          tipo: "SUCCESSIONE_ZONA",
        },
      },
      // Arco tra menzioni
      {
        data: {
          id: "arco2",
          source: "menzione1",
          target: "menzione2",
          tipo: "COLLEGATO",
        },
      },
    ],
  };

  describe("isKernelCategoriaNode", () => {
    it("should identify KernelCategoria nodes correctly", () => {
      expect(isKernelCategoriaNode(mockElements.nodes[0])).toBe(true);
      expect(isKernelCategoriaNode(mockElements.nodes[1])).toBe(true);
      expect(isKernelCategoriaNode(mockElements.nodes[2])).toBe(false);
      expect(isKernelCategoriaNode(mockElements.nodes[3])).toBe(false);
    });
  });

  describe("filterEntitaElements", () => {
    it("should return only KernelCategoria nodes in overview mode", () => {
      const result = filterEntitaElements(mockElements, null);
      
      expect(result.nodes.length).toBe(2);
      expect(result.nodes.every(node => node.data.tipo === "KernelCategoria")).toBe(true);
      
      // Controlla che gli archi siano quelli tra categorie
      expect(result.edges.length).toBe(1);
      expect(result.edges[0].data.tipo).toBe("SUCCESSIONE_ZONA");
    });

    it("should return focused category and its menzioni in detail mode", () => {
      const result = filterEntitaElements(mockElements, "categoria1");
      
      expect(result.nodes.length).toBe(2); // categoria1 + menzione1
      expect(result.nodes[0].data.id).toBe("categoria1");
      expect(result.nodes[1].data.id).toBe("menzione1");
      
      // Controlla che gli archi siano tra i nodi selezionati
      expect(result.edges.length).toBe(0); // Nessun arco tra menzioni in questo caso
    });

    it("should return empty elements when no matching category exists", () => {
      const result = filterEntitaElements(mockElements, "categoria3");
      
      expect(result.nodes.length).toBe(2);
      expect(result.edges.length).toBe(1); // Arco tra categorie esistenti
    });
  });
});