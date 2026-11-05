import { expect, test } from "vitest";

import { packageName } from "./index";

test("the placeholder exports the package name", () => {
  expect(packageName).toBe("@bowerstone/ui");
});
