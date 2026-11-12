"""
LeetCode-Style Problem Tools Plugin for LiuHao AI OS

Provides problem template generation, test case generation,
complexity analysis helper, code templates for different languages,
problem categories, and solution structure.

Capabilities:
- generate_template: Generate problem template
- generate_test_cases: Generate test cases
- complexity_analysis: Help analyze time/space complexity
- code_template: Get code template for a language
- get_categories: Get problem categories
- solution_structure: Get solution structure outline
- difficulty_levels: Get difficulty level info
- generate_readme: Generate problem README
"""

import re
import random
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class ProblemTemplate:
    """A generated problem template."""
    title: str
    difficulty: str
    description: str
    examples: List[Dict[str, str]]
    constraints: List[str]
    follow_up: str
    categories: List[str]


@dataclass
class TestCase:
    """A test case for a problem."""
    id: int
    input: Dict[str, Any]
    expected_output: Any
    description: str
    is_edge_case: bool


@dataclass
class ComplexityAnalysis:
    """Complexity analysis result."""
    time_complexity: str
    space_complexity: str
    explanation: str
    approach: str


class LeetcodePlugin(BasePlugin):
    """LeetCode-style problem tools plugin.

    Provides problem templates, test case generation,
    complexity analysis helpers, and code templates.

    Example:
        plugin = LeetcodePlugin()
        plugin.initialize()
        plugin.activate()

        template = plugin.generate_template("Two Sum", difficulty="easy")
        print(template.description)
    """

    # Problem categories
    CATEGORIES = [
        "Array", "String", "Hash Table", "Dynamic Programming",
        "Math", "Sorting", "Greedy", "Depth-First Search",
        "Breadth-First Search", "Binary Search", "Tree",
        "Binary Tree", "Linked List", "Stack", "Queue",
        "Heap/Priority Queue", "Graph", "Two Pointers",
        "Sliding Window", "Backtracking", "Recursion",
        "Divide and Conquer", "Bit Manipulation", "Trie",
        "Union Find", "Design", "Matrix", "Simulation",
    ]

    # Difficulty levels with color coding
    DIFFICULTIES = {
        "easy": {"color": "green", "acceptance_rate": "70-80%", "typical_complexity": "O(n) or O(n log n)"},
        "medium": {"color": "orange", "acceptance_rate": "40-60%", "typical_complexity": "O(n^2) or O(n log n)"},
        "hard": {"color": "red", "acceptance_rate": "20-40%", "typical_complexity": "O(n^2) or higher, or tricky optimizations"},
    }

    # Code templates for different languages
    CODE_TEMPLATES: Dict[str, str] = {
        "python": '''class Solution:
    def {function_name}(self, {params}) -> {return_type}:
        """
        {description}

        Args:
            {arg_docs}

        Returns:
            {return_doc}
        """
        # Your code here
        pass
''',
        "javascript": '''/**
 * {description}
 * @param {param_types}
 * @return {return_type}
 */
var {function_name} = function({params}) {{
    // Your code here
}};
''',
        "java": '''class Solution {{
    public {return_type} {function_name}({params}) {{
        // Your code here
    }}
}}
''',
        "cpp": '''class Solution {{
public:
    {return_type} {function_name}({params}) {{
        // Your code here
    }}
}};
''',
        "go": '''func {function_name}({params}) {return_type} {{
    // Your code here
}}
''',
        "rust": '''impl Solution {{
    pub fn {function_name}({params}) -> {return_type} {{
        // Your code here
    }}
}}
''',
        "csharp": '''public class Solution {{
    public {return_type} {function_name}({params}) {{
        // Your code here
    }}
}}
''',
        "kotlin": '''class Solution {{
    fun {function_name}({params}): {return_type} {{
        // Your code here
    }}
}}
''',
        "swift": '''class Solution {{
    func {function_name}({params}) -> {return_type} {{
        // Your code here
    }}
}}
''',
    }

    # Common constraints by category
    CONSTRAINTS_BY_CATEGORY = {
        "Array": [
            "1 <= nums.length <= 10^5",
            "-10^9 <= nums[i] <= 10^9",
            "nums contains distinct values",
        ],
        "String": [
            "1 <= s.length <= 10^5",
            "s consists of lowercase English letters",
            "s contains only printable ASCII characters",
        ],
        "Tree": [
            "The number of nodes in the tree is in the range [1, 10^4]",
            "-100 <= Node.val <= 100",
            "The tree is not necessarily balanced",
        ],
        "Linked List": [
            "The number of nodes in the list is in the range [1, 1000]",
            "-10^6 <= Node.val <= 10^6",
            "The list is singly-linked",
        ],
        "Graph": [
            "1 <= n <= 100",
            "0 <= edges.length <= n * (n - 1) / 2",
            "There are no self-loops or repeated edges",
        ],
    }

    def __init__(self) -> None:
        """Initialize the LeetCode plugin."""
        metadata = PluginMetadata(
            name="leetcode",
            version="1.0.0",
            description="LeetCode-style problem templates and tools",
            author="LiuHao AI OS Team",
            categories=["development", "education", "coding"],
            keywords=["leetcode", "algorithm", "coding", "interview", "problem"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_language: str = "python"
        self._default_difficulty: str = "easy"

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the LeetCode plugin."""
        try:
            if config:
                self._default_language = config.get("default_language", "python")
                self._default_difficulty = config.get("default_difficulty", "easy")

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the LeetCode plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the LeetCode plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the LeetCode plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "generate_template",
            "generate_test_cases",
            "complexity_analysis",
            "code_template",
            "get_categories",
            "solution_structure",
            "difficulty_levels",
            "generate_readme",
            "supported_languages",
        ]

    # ==================== Template Generation ====================

    def generate_template(
        self,
        title: str,
        *,
        difficulty: Optional[str] = None,
        categories: Optional[List[str]] = None,
        description_hint: str = "",
    ) -> ProblemTemplate:
        """Generate a problem template.

        Args:
            title: Problem title.
            difficulty: Difficulty level (easy, medium, hard).
            categories: Problem categories.
            description_hint: Hint for generating description.

        Returns:
            ProblemTemplate with full problem description.
        """
        self._ensure_active()
        self._increment_call_count()

        diff = (difficulty or self._default_difficulty).lower()
        if diff not in self.DIFFICULTIES:
            raise ValueError(f"Unknown difficulty: {diff}. Use: {list(self.DIFFICULTIES.keys())}")

        cats = categories or []
        if not cats:
            cats = random.sample(self.CATEGORIES, min(3, len(self.CATEGORIES)))

        # Generate description
        slug = self._title_to_slug(title)
        function_name = self._title_to_function(title)

        description = self._generate_description(title, diff, cats, description_hint)

        # Generate examples
        examples = self._generate_examples(cats, diff)

        # Generate constraints
        constraints = self._generate_constraints(cats, diff)

        # Generate follow-up
        follow_up = self._generate_follow_up(cats, diff)

        return ProblemTemplate(
            title=title,
            difficulty=diff,
            description=description,
            examples=examples,
            constraints=constraints,
            follow_up=follow_up,
            categories=cats,
        )

    def _title_to_slug(self, title: str) -> str:
        """Convert title to URL slug."""
        slug = title.lower()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"\s+", "-", slug)
        return slug

    def _title_to_function(self, title: str) -> str:
        """Convert title to camelCase function name."""
        words = re.findall(r"\w+", title)
        if not words:
            return "solve"
        camel = words[0].lower()
        for word in words[1:]:
            camel += word.capitalize()
        return camel

    def _generate_description(
        self, title: str, difficulty: str, categories: List[str], hint: str
    ) -> str:
        """Generate a problem description."""
        cat_str = "/".join(categories[:2]) if categories else "programming"

        description_parts = [
            f"# {title}",
            "",
            f"Given an input related to {cat_str.lower()}, implement a solution.",
            "",
        ]

        if hint:
            description_parts.append(f"## Problem Description")
            description_parts.append("")
            description_parts.append(hint)
            description_parts.append("")

        description_parts.append(f"## Example 1")
        description_parts.append("")
        description_parts.append("**Input:** ...")
        description_parts.append("**Output:** ...")
        description_parts.append("**Explanation:** ...")
        description_parts.append("")

        return "\n".join(description_parts)

    def _generate_examples(
        self, categories: List[str], difficulty: str
    ) -> List[Dict[str, str]]:
        """Generate example placeholders."""
        num_examples = 2 if difficulty == "easy" else 3
        examples = []

        for i in range(1, num_examples + 1):
            examples.append({
                "id": str(i),
                "input": "nums = [...], target = ...",
                "output": "...",
                "explanation": "...",
            })

        return examples

    def _generate_constraints(
        self, categories: List[str], difficulty: str
    ) -> List[str]:
        """Generate constraints based on categories and difficulty."""
        constraints = []

        for cat in categories:
            if cat in self.CONSTRAINTS_BY_CATEGORY:
                constraints.extend(self.CONSTRAINTS_BY_CATEGORY[cat][:2])

        if not constraints:
            constraints = [
                "1 <= n <= 10^5",
                "-10^9 <= element <= 10^9",
            ]

        # Adjust based on difficulty
        if difficulty == "hard":
            constraints = [c.replace("10^5", "10^6") for c in constraints]
        elif difficulty == "easy":
            constraints = [c.replace("10^5", "10^3") for c in constraints]

        return list(dict.fromkeys(constraints))  # Remove duplicates

    def _generate_follow_up(self, categories: List[str], difficulty: str) -> str:
        """Generate a follow-up question."""
        if difficulty == "easy":
            return "Can you solve it in O(n) time and O(1) space?"
        elif difficulty == "medium":
            return "Can you solve it in O(n log n) time? What if the input is very large?"
        else:
            return "Can you optimize further? What if there are multiple constraints to satisfy?"

    # ==================== Test Cases ====================

    def generate_test_cases(
        self,
        problem_type: str = "array",
        *,
        count: int = 5,
        include_edge_cases: bool = True,
    ) -> List[TestCase]:
        """Generate test cases for a problem type.

        Args:
            problem_type: Type of problem (array, string, tree, etc.).
            count: Number of test cases to generate.
            include_edge_cases: Whether to include edge cases.

        Returns:
            List of TestCase objects.
        """
        self._ensure_active()
        self._increment_call_count()

        test_cases: List[TestCase] = []
        case_id = 1

        # Edge cases
        if include_edge_cases:
            edge_cases = self._generate_edge_cases(problem_type)
            for ec in edge_cases:
                ec["id"] = case_id
                test_cases.append(TestCase(
                    id=case_id,
                    input=ec.get("input", {}),
                    expected_output=ec.get("output", None),
                    description=ec.get("description", ""),
                    is_edge_case=True,
                ))
                case_id += 1

        # Normal test cases
        normal_count = max(0, count - len(test_cases))
        for _ in range(normal_count):
            tc_input = self._generate_test_input(problem_type)
            test_cases.append(TestCase(
                id=case_id,
                input=tc_input,
                expected_output="TBD",
                description=f"Normal test case {case_id}",
                is_edge_case=False,
            ))
            case_id += 1

        return test_cases

    def _generate_edge_cases(self, problem_type: str) -> List[Dict[str, Any]]:
        """Generate edge cases for a problem type."""
        edge_cases = []

        if problem_type.lower() in ("array", "list"):
            edge_cases = [
                {"input": {"nums": []}, "output": [], "description": "Empty array"},
                {"input": {"nums": [1]}, "output": [1], "description": "Single element"},
                {"input": {"nums": [1, 1, 1]}, "output": "TBD", "description": "All same elements"},
                {"input": {"nums": list(range(1000))}, "output": "TBD", "description": "Large array"},
                {"input": {"nums": [5, 4, 3, 2, 1]}, "output": "TBD", "description": "Reverse sorted"},
            ]
        elif problem_type.lower() == "string":
            edge_cases = [
                {"input": {"s": ""}, "output": "", "description": "Empty string"},
                {"input": {"s": "a"}, "output": "a", "description": "Single character"},
                {"input": {"s": "aaaaa"}, "output": "TBD", "description": "All same characters"},
                {"input": {"s": "abcdefghij" * 1000}, "output": "TBD", "description": "Long string"},
            ]
        else:
            edge_cases = [
                {"input": {"n": 0}, "output": 0, "description": "Zero input"},
                {"input": {"n": 1}, "output": 1, "description": "Minimum valid input"},
                {"input": {"n": 1000000}, "output": "TBD", "description": "Large input"},
            ]

        return edge_cases

    def _generate_test_input(self, problem_type: str) -> Dict[str, Any]:
        """Generate a random test input."""
        if problem_type.lower() in ("array", "list"):
            length = random.randint(2, 20)
            nums = [random.randint(-100, 100) for _ in range(length)]
            target = random.choice(nums) if random.random() > 0.5 else random.randint(-100, 100)
            return {"nums": nums, "target": target}
        elif problem_type.lower() == "string":
            length = random.randint(3, 20)
            chars = "abcdefghijklmnopqrstuvwxyz"
            s = "".join(random.choice(chars) for _ in range(length))
            return {"s": s}
        else:
            return {"n": random.randint(1, 100)}

    # ==================== Complexity Analysis ====================

    def complexity_analysis(
        self,
        approach: str,
        problem_category: str = "array",
        *,
        input_size: str = "n",
    ) -> ComplexityAnalysis:
        """Help analyze time and space complexity.

        Args:
            approach: Description of the approach.
            problem_category: Category of problem.
            input_size: Variable name for input size.

        Returns:
            ComplexityAnalysis with estimated complexity.
        """
        self._ensure_active()
        self._increment_call_count()

        approach_lower = approach.lower()

        # Analyze based on keywords in approach description
        time_complexity = "O(n)"
        space_complexity = "O(1)"
        explanation = "Based on the approach description:"

        factors: List[str] = []

        # Time complexity indicators
        if any(kw in approach_lower for kw in ("brute force", "nested loop", "two loops", "o(n^2)", "n squared")):
            time_complexity = "O(n^2)"
            factors.append("Nested loops suggest quadratic time")
        elif any(kw in approach_lower for kw in ("binary search", "divide and conquer", "merge sort")):
            time_complexity = "O(n log n)"
            factors.append("Divide and conquer suggests n log n time")
        elif any(kw in approach_lower for kw in ("heap", "priority queue")):
            time_complexity = "O(n log k)"
            factors.append("Heap operations add log factor")
        elif any(kw in approach_lower for kw in ("hash map", "hash table", "dictionary", "one pass", "single pass", "two pointers", "sliding window")):
            time_complexity = "O(n)"
            factors.append("Single pass with hash map / pointers suggests linear time")
        elif any(kw in approach_lower for kw in ("dp", "dynamic programming", "memoization")):
            time_complexity = "O(n)"
            space_complexity = "O(n)"
            factors.append("DP typically uses linear time and space")
        elif any(kw in approach_lower for kw in ("dfs", "bfs", "depth first", "breadth first", "graph")):
            time_complexity = "O(V + E)"
            space_complexity = "O(V)"
            factors.append("Graph traversal visits each node and edge once")
        elif any(kw in approach_lower for kw in ("recursion", "backtracking")):
            time_complexity = "O(2^n)"
            space_complexity = "O(n)"
            factors.append("Backtracking can have exponential time complexity")
        elif any(kw in approach_lower for kw in ("trie", "prefix tree")):
            time_complexity = "O(n * L)"
            space_complexity = "O(n * L)"
            factors.append("Trie operations depend on string length")
        else:
            factors.append("Linear scan is assumed")

        # Space complexity indicators
        if any(kw in approach_lower for kw in ("in place", "constant space", "o(1) space", "no extra space")):
            space_complexity = "O(1)"
            factors.append("In-place algorithm uses constant space")
        elif any(kw in approach_lower for kw in ("hash map", "hash set", "dictionary", "set")):
            if space_complexity == "O(1)":
                space_complexity = "O(n)"
            factors.append("Hash map/set may use up to O(n) space")
        elif any(kw in approach_lower for kw in ("recursion", "stack")):
            if space_complexity == "O(1)":
                space_complexity = "O(n)"
            factors.append("Recursion stack uses O(n) space in worst case")

        explanation += "\n- " + "\n- ".join(factors)

        return ComplexityAnalysis(
            time_complexity=time_complexity,
            space_complexity=space_complexity,
            explanation=explanation,
            approach=approach,
        )

    # ==================== Code Templates ====================

    def code_template(
        self,
        function_name: str,
        *,
        language: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
        return_type: str = "int",
        description: str = "",
    ) -> str:
        """Get a code template for a specific language.

        Args:
            function_name: Name of the function.
            language: Programming language.
            params: Dict of parameter name -> type.
            return_type: Return type.
            description: Function description.

        Returns:
            Code template string.
        """
        self._ensure_active()
        self._increment_call_count()

        lang = (language or self._default_language).lower()
        if lang not in self.CODE_TEMPLATES:
            raise ValueError(
                f"Unsupported language: {lang}. "
                f"Supported: {list(self.CODE_TEMPLATES.keys())}"
            )

        template = self.CODE_TEMPLATES[lang]

        # Build params string
        if params:
            param_list = [f"{t} {name}" if lang not in ("python", "go", "rust") else f"{name}: {t}"
                          for name, t in params.items()]
            params_str = ", ".join(param_list)
            param_type_str = ", ".join(f"{t} {name}" for name, t in params.items())
            arg_docs = "\n            ".join(f"{name}: {t}" for name, t in params.items())
        else:
            params_str = ""
            param_type_str = ""
            arg_docs = ""

        # Format template
        template = template.replace("{function_name}", function_name)
        template = template.replace("{params}", params_str)
        template = template.replace("{param_types}", param_type_str)
        template = template.replace("{return_type}", return_type)
        template = template.replace("{description}", description or function_name)
        template = template.replace("{arg_docs}", arg_docs)
        template = template.replace("{return_doc}", return_type)

        return template

    def supported_languages(self) -> List[str]:
        """Get list of supported languages for code templates.

        Returns:
            List of language names.
        """
        return list(self.CODE_TEMPLATES.keys())

    # ==================== Categories & Difficulty ====================

    def get_categories(self) -> List[str]:
        """Get all problem categories.

        Returns:
            List of category names.
        """
        self._ensure_active()
        self._increment_call_count()
        return list(self.CATEGORIES)

    def difficulty_levels(self) -> Dict[str, Dict[str, str]]:
        """Get difficulty level information.

        Returns:
            Dict of difficulty info.
        """
        self._ensure_active()
        self._increment_call_count()
        return dict(self.DIFFICULTIES)

    # ==================== Solution Structure ====================

    def solution_structure(self, problem_type: str = "array") -> Dict[str, Any]:
        """Get a recommended solution structure outline.

        Args:
            problem_type: Type of problem.

        Returns:
            Dict with solution structure components.
        """
        self._ensure_active()
        self._increment_call_count()

        structures = {
            "array": {
                "approaches": [
                    {"name": "Brute Force", "time": "O(n^2)", "space": "O(1)"},
                    {"name": "Hash Map", "time": "O(n)", "space": "O(n)"},
                    {"name": "Two Pointers", "time": "O(n)", "space": "O(1)"},
                    {"name": "Sliding Window", "time": "O(n)", "space": "O(1)"},
                    {"name": "Binary Search", "time": "O(log n)", "space": "O(1)"},
                    {"name": "Sorting", "time": "O(n log n)", "space": "O(1) or O(n)"},
                ],
                "steps": [
                    "Clarify the problem and constraints",
                    "Consider edge cases (empty, single element, duplicates)",
                    "Start with brute force solution",
                    "Optimize using appropriate technique",
                    "Test with multiple cases",
                ],
            },
            "string": {
                "approaches": [
                    {"name": "Brute Force", "time": "O(n^2)", "space": "O(1)"},
                    {"name": "Hash Map / Array", "time": "O(n)", "space": "O(1)"},
                    {"name": "Two Pointers", "time": "O(n)", "space": "O(1)"},
                    {"name": "Sliding Window", "time": "O(n)", "space": "O(k)"},
                    {"name": "DP", "time": "O(n^2)", "space": "O(n^2)"},
                ],
                "steps": [
                    "Clarify character set (ASCII, Unicode, etc.)",
                    "Consider empty string edge case",
                    "Check if case sensitivity matters",
                    "Use array for 26/256 char problems",
                ],
            },
            "tree": {
                "approaches": [
                    {"name": "Recursive DFS", "time": "O(n)", "space": "O(h)"},
                    {"name": "Iterative DFS", "time": "O(n)", "space": "O(h)"},
                    {"name": "BFS", "time": "O(n)", "space": "O(w)"},
                    {"name": "Morris Traversal", "time": "O(n)", "space": "O(1)"},
                ],
                "steps": [
                    "Determine if tree is BST or general",
                    "Consider null node edge cases",
                    "Think about recursive vs iterative",
                    "Handle level-order with queue",
                ],
            },
            "graph": {
                "approaches": [
                    {"name": "DFS", "time": "O(V+E)", "space": "O(V)"},
                    {"name": "BFS", "time": "O(V+E)", "space": "O(V)"},
                    {"name": "Dijkstra", "time": "O((V+E) log V)", "space": "O(V)"},
                    {"name": "Union Find", "time": "O(alpha(n))", "space": "O(n)"},
                    {"name": "Topological Sort", "time": "O(V+E)", "space": "O(V)"},
                ],
                "steps": [
                    "Clarify directed or undirected",
                    "Check for cycles",
                    "Consider connected components",
                    "Choose DFS for paths, BFS for shortest",
                ],
            },
            "dp": {
                "approaches": [
                    {"name": "Top-down (Memoization)", "time": "O(n)", "space": "O(n)"},
                    {"name": "Bottom-up (Tabulation)", "time": "O(n)", "space": "O(n)"},
                    {"name": "Space Optimized", "time": "O(n)", "space": "O(1)"},
                ],
                "steps": [
                    "Identify overlapping subproblems",
                    "Define DP state clearly",
                    "Find recurrence relation",
                    "Set up base cases",
                    "Optimize space if possible",
                ],
            },
        }

        pt = problem_type.lower()
        if pt in structures:
            return structures[pt]

        # Generic structure
        return {
            "approaches": [
                {"name": "Brute Force", "time": "O(n^2)", "space": "O(1)"},
                {"name": "Optimized", "time": "O(n)", "space": "O(n)"},
                {"name": "Optimal", "time": "O(n)", "space": "O(1)"},
            ],
            "steps": [
                "Understand the problem thoroughly",
                "Consider edge cases",
                "Start with brute force",
                "Identify optimization opportunities",
                "Test thoroughly",
            ],
        }

    # ==================== README Generation ====================

    def generate_readme(
        self,
        problem_name: str,
        *,
        difficulty: str = "easy",
        topics: Optional[List[str]] = None,
    ) -> str:
        """Generate a problem README file.

        Args:
            problem_name: Name of the problem.
            difficulty: Difficulty level.
            topics: List of topic tags.

        Returns:
            README markdown string.
        """
        self._ensure_active()
        self._increment_call_count()

        diff = difficulty.capitalize()
        topics_str = ", ".join(topics) if topics else "General"

        slug = self._title_to_slug(problem_name)

        readme = f"""# {problem_name}

| Difficulty | Topics |
|------------|--------|
| {diff} | {topics_str} |

## Problem Statement

<!-- Add problem description here -->

## Examples

### Example 1
```
Input: ...
Output: ...
Explanation: ...
```

### Example 2
```
Input: ...
Output: ...
Explanation: ...
```

## Constraints

- Constraint 1
- Constraint 2

## Solution

### Approach

<!-- Describe your approach here -->

### Complexity Analysis

- **Time Complexity:** O(...)
- **Space Complexity:** O(...)

### Code

```python
class Solution:
    def {self._title_to_function(problem_name)}(self, ...) -> ...:
        pass
```

## Notes

<!-- Additional notes or follow-up questions -->
"""
        return readme

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_language": self._default_language,
            "default_difficulty": self._default_difficulty,
            "categories": len(self.CATEGORIES),
            "supported_languages": len(self.CODE_TEMPLATES),
            "difficulty_levels": list(self.DIFFICULTIES.keys()),
        }
