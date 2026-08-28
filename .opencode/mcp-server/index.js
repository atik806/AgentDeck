import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { execSync, exec } from "node:child_process";
import { readFileSync, readdirSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { promisify } from "node:util";

const execAsync = promisify(exec);

const FRONTEND_DIR = "/run/media/atik/New Volume/Website for selling products demo/dhaka-wholesale-frontend";
const BACKEND_DIR = "/run/media/atik/New Volume/Website for selling products demo/Dhaka_wholesale-backend";

const PROJECTS = {
  frontend: { dir: FRONTEND_DIR, label: "Frontend (Next.js)" },
  backend: { dir: BACKEND_DIR, label: "Backend (NestJS)" },
};

function readPackageJson(dir) {
  try {
    return JSON.parse(readFileSync(join(dir, "package.json"), "utf8"));
  } catch {
    return null;
  }
}

function getProjectInfo(project) {
  const pkg = readPackageJson(project.dir);
  return {
    name: pkg?.name ?? "unknown",
    version: pkg?.version ?? "unknown",
    scripts: pkg?.scripts ?? {},
    dependencies: pkg?.dependencies ?? {},
    devDependencies: pkg?.devDependencies ?? {},
  };
}

async function runCommand(command, cwd, timeout = 120000) {
  try {
    const { stdout, stderr } = await execAsync(command, { cwd, timeout });
    return { success: true, stdout: stdout.trim(), stderr: stderr.trim() };
  } catch (err) {
    return {
      success: false,
      stdout: err.stdout?.trim() ?? "",
      stderr: err.stderr?.trim() ?? err.message,
      code: err.code,
    };
  }
}

const server = new Server(
  { name: "dhaka-wholesale-mcp", version: "1.0.0" },
  { capabilities: { tools: {}, resources: {} } },
);

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [
    {
      name: "get_project_status",
      description: "Get build status, scripts, and info for both frontend and backend projects",
      inputSchema: {
        type: "object",
        properties: {
          project: { type: "string", enum: ["frontend", "backend", "all"], description: "Which project to inspect" },
        },
        required: ["project"],
      },
    },
    {
      name: "frontend_build",
      description: "Run npm run build on the frontend (Next.js)",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "frontend_typecheck",
      description: "Run TypeScript type checking (npx tsc --noEmit) on the frontend",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "frontend_lint",
      description: "Run ESLint on the frontend project",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "backend_build",
      description: "Run npm run build on the backend (NestJS)",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "backend_lint",
      description: "Run ESLint on the backend project",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "backend_test",
      description: "Run npm test on the backend project",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "read_database_schema",
      description: "Read the database schema SQL files from the backend",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "list_api_routes",
      description: "List all API route files in both frontend (api/) and backend (src/)",
      inputSchema: {
        type: "object",
        properties: {
          project: { type: "string", enum: ["frontend", "backend", "all"] },
        },
        required: ["project"],
      },
    },
    {
      name: "list_migrations",
      description: "List database migration SQL files in the backend",
      inputSchema: {
        type: "object",
        properties: {},
      },
    },
    {
      name: "run_script",
      description: "Run any npm script from either project's package.json",
      inputSchema: {
        type: "object",
        properties: {
          project: { type: "string", enum: ["frontend", "backend"] },
          script: { type: "string", description: "The npm script name (e.g. build, dev, start, lint, test)" },
        },
        required: ["project", "script"],
      },
    },
    {
      name: "read_file",
      description: "Read a file from either project (relative path)",
      inputSchema: {
        type: "object",
        properties: {
          project: { type: "string", enum: ["frontend", "backend"] },
          path: { type: "string", description: "Relative path from project root (e.g. src/app/page.tsx)" },
        },
        required: ["project", "path"],
      },
    },
    {
      name: "read_project_config",
      description: "Read a configuration file from either project (tsconfig, package.json, nest-cli, vercel, eslint)",
      inputSchema: {
        type: "object",
        properties: {
          project: { type: "string", enum: ["frontend", "backend"] },
          config: {
            type: "string",
            enum: ["package.json", "tsconfig.json", "vercel.json", "nest-cli.json", "eslint.config.mjs"],
          },
        },
        required: ["project", "config"],
      },
    },
  ],
}));

server.setRequestHandler(CallToolRequestSchema, async (request) => {
  const { name, arguments: args } = request.params;
  const project = args?.project ?? "all";

  switch (name) {
    case "get_project_status": {
      const targets = project === "all" ? ["frontend", "backend"] : [project];
      const results = {};
      for (const p of targets) {
        const info = getProjectInfo(PROJECTS[p]);
        info.dir = PROJECTS[p].dir;
        info.scripts = Object.keys(info.scripts);
        info.dependencyCount = Object.keys(info.dependencies).length;
        info.devDependencyCount = Object.keys(info.devDependencies).length;
        delete info.dependencies;
        delete info.devDependencies;
        results[p] = info;
      }
      return { content: [{ type: "text", text: JSON.stringify(results, null, 2) }] };
    }

    case "frontend_build": {
      const result = await runCommand("npm run build", FRONTEND_DIR);
      return { content: [{ type: "text", text: result.success ? result.stdout : `FAILED:\n${result.stderr}` }] };
    }

    case "frontend_typecheck": {
      const result = await runCommand("npx tsc --noEmit", FRONTEND_DIR);
      return { content: [{ type: "text", text: result.success ? result.stdout || "No type errors" : `Type errors found:\n${result.stderr}` }] };
    }

    case "frontend_lint": {
      const result = await runCommand("npm run lint", FRONTEND_DIR);
      return { content: [{ type: "text", text: result.success ? result.stdout || "Lint passed" : result.stderr }] };
    }

    case "backend_build": {
      const result = await runCommand("npm run build", BACKEND_DIR);
      return { content: [{ type: "text", text: result.success ? result.stdout : `FAILED:\n${result.stderr}` }] };
    }

    case "backend_lint": {
      const result = await runCommand("npm run lint", BACKEND_DIR);
      return { content: [{ type: "text", text: result.success ? result.stdout || "Lint passed" : result.stderr }] };
    }

    case "backend_test": {
      const result = await runCommand("npm test", BACKEND_DIR);
      return { content: [{ type: "text", text: result.success ? result.stdout : `FAILED:\n${result.stderr}` }] };
    }

    case "read_database_schema": {
      const schemaFiles = [];
      for (const file of ["supabase-schema.sql", "supabase-migration.sql"]) {
        const path = join(BACKEND_DIR, file);
        if (existsSync(path)) {
          schemaFiles.push({ file, content: readFileSync(path, "utf8") });
        }
      }
      return { content: [{ type: "text", text: JSON.stringify(schemaFiles, null, 2) }] };
    }

    case "list_api_routes": {
      const targets = project === "all" ? ["frontend", "backend"] : [project];
      const allRoutes = {};
      if (targets.includes("frontend")) {
        const apiDir = join(FRONTEND_DIR, "api");
        allRoutes.frontend = existsSync(apiDir) ? walkDir(apiDir, "api") : [];
      }
      if (targets.includes("backend")) {
        const srcDir = join(BACKEND_DIR, "src");
        allRoutes.backend = existsSync(srcDir) ? walkDir(srcDir, "src").filter(f => f.endsWith(".ts") && !f.endsWith(".spec.ts") && !f.endsWith(".test.ts")) : [];
      }
      return { content: [{ type: "text", text: JSON.stringify(allRoutes, null, 2) }] };
    }

    case "list_migrations": {
      const migrations = [];
      const supabaseDir = join(BACKEND_DIR, "supabase");
      if (existsSync(supabaseDir)) {
        for (const file of readdirSync(supabaseDir)) {
          if (file.endsWith(".sql")) {
            migrations.push(file);
          }
        }
      }
      return { content: [{ type: "text", text: JSON.stringify(migrations, null, 2) }] };
    }

    case "run_script": {
      const target = PROJECTS[args.project];
      const result = await runCommand(`npm run ${args.script}`, target.dir);
      return { content: [{ type: "text", text: result.success ? result.stdout : `FAILED:\n${result.stderr}` }] };
    }

    case "read_file": {
      const target = PROJECTS[args.project];
      const fullPath = resolve(target.dir, args.path);
      if (!fullPath.startsWith(target.dir)) {
        return { content: [{ type: "text", text: "Error: Path traversal detected" }], isError: true };
      }
      try {
        const content = readFileSync(fullPath, "utf8");
        return { content: [{ type: "text", text: content }] };
      } catch (err) {
        return { content: [{ type: "text", text: `Error reading file: ${err.message}` }], isError: true };
      }
    }

    case "read_project_config": {
      const target = PROJECTS[args.project];
      const configPath = join(target.dir, args.config);
      if (!existsSync(configPath)) {
        return { content: [{ type: "text", text: `Config file not found: ${args.config}` }], isError: true };
      }
      const content = readFileSync(configPath, "utf8");
      return { content: [{ type: "text", text: content }] };
    }

    default:
      return { content: [{ type: "text", text: `Unknown tool: ${name}` }], isError: true };
  }
});

function walkDir(dir, prefix) {
  const files = [];
  try {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const fullPath = join(dir, entry.name);
      const relPath = join(prefix, entry.name);
      if (entry.isDirectory()) {
        if (!entry.name.startsWith(".") && entry.name !== "node_modules") {
          files.push(...walkDir(fullPath, relPath));
        }
      } else {
        files.push(relPath);
      }
    }
  } catch {}
  return files;
}

const transport = new StdioServerTransport();
await server.connect(transport);
