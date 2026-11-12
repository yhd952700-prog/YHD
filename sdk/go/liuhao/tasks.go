package liuhao

import (
	"context"
	"fmt"
	"net/http"
)

// TasksService provides methods for managing asynchronous tasks in the LiuHao AI OS.
//
// Use Client.Tasks to access this service. Tasks represent long-running
// operations that execute asynchronously, such as document processing,
// batch operations, and data imports.
type TasksService struct {
	client *Client
}

// Create creates a new asynchronous task.
//
// The Type field is required and determines the kind of task to execute.
// Task-specific parameters can be passed via the Params field.
//
// Example:
//
//	task, err := client.Tasks.Create(ctx, &liuhao.TaskCreateOptions{
//	    Type:        "document.process",
//	    Description: "Process uploaded PDF document",
//	    Params: map[string]interface{}{
//	        "document_id": "doc_abc123",
//	        "chunk_size":  1000,
//	    },
//	    MaxRetries: 3,
//	})
func (s *TasksService) Create(ctx context.Context, opts *TaskCreateOptions) (*Task, error) {
	if opts == nil {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_options",
				Message:    "task create options must not be nil",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts.Type == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_type",
				Message:    "task type is required",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := "/tasks"

	var result Task
	if err := s.client.request(ctx, http.MethodPost, path, opts, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Get retrieves a single task by its ID.
//
// Returns a NotFoundError if the task does not exist.
//
// Example:
//
//	task, err := client.Tasks.Get(ctx, "task_abc123")
//	if liuhao.IsNotFoundError(err) {
//	    fmt.Println("Task not found")
//	}
//	fmt.Printf("Task status: %s, progress: %d%%\n", task.Status, task.Progress)
func (s *TasksService) Get(ctx context.Context, id string) (*Task, error) {
	if id == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "task ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/tasks/%s", id)

	var result Task
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// List retrieves a paginated list of tasks.
//
// The opts parameter controls pagination, sorting, and filtering.
// Pass nil to use default options. Use the Query field to filter by
// task type or description.
//
// Example:
//
//	resp, err := client.Tasks.List(ctx, &liuhao.ListOptions{
//	    Page:     1,
//	    PageSize: 20,
//	    Sort:     "created_at",
//	    Order:    "desc",
//	})
func (s *TasksService) List(ctx context.Context, opts *ListOptions) (*TaskListResponse, error) {
	path := "/tasks"
	query := opts.toQueryParams()
	path = addQueryParams(path, query)

	var result TaskListResponse
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Cancel cancels a running or pending task.
//
// If the task is already completed or failed, an appropriate error is
// returned. Cancellation is best-effort; the task may finish before
// the cancellation takes effect.
//
// Example:
//
//	err := client.Tasks.Cancel(ctx, "task_abc123")
//	if err != nil {
//	    fmt.Printf("Failed to cancel task: %v\n", err)
//	}
func (s *TasksService) Cancel(ctx context.Context, id string) error {
	if id == "" {
		return &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "task ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/tasks/%s/cancel", id)

	if err := s.client.request(ctx, http.MethodPost, path, nil, nil); err != nil {
		return err
	}
	return nil
}

// Status retrieves the current status of a task.
//
// This is a convenience method that returns just the status. Use Get
// to retrieve full task details including progress and result.
//
// Example:
//
//	status, err := client.Tasks.Status(ctx, "task_abc123")
//	if status == liuhao.TaskStatusCompleted {
//	    fmt.Println("Task completed successfully")
//	}
func (s *TasksService) Status(ctx context.Context, id string) (TaskStatus, error) {
	task, err := s.Get(ctx, id)
	if err != nil {
		return TaskStatusUnknown, err
	}
	return task.Status, nil
}

// Results retrieves the results of a completed task.
//
// This method returns the task result data. It is equivalent to calling
// Get and accessing the Result field, but provides a more semantic API.
// Returns an error if the task has not completed yet.
//
// Example:
//
//	result, err := client.Tasks.Results(ctx, "task_abc123")
//	if err != nil {
//	    if liuhao.IsAPIError(err) {
//	        fmt.Println("Task may not be completed yet")
//	    }
//	    return
//	}
//	fmt.Printf("Task result: %v\n", result)
func (s *TasksService) Results(ctx context.Context, id string) (interface{}, error) {
	task, err := s.Get(ctx, id)
	if err != nil {
		return nil, err
	}
	if task.Status != TaskStatusCompleted {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "task_not_completed",
				Message:    fmt.Sprintf("task is not completed (status: %s)", task.Status),
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	return task.Result, nil
}

// Retry retries a failed task.
//
// This creates a new execution of the task with the same parameters.
// Only tasks that have failed can be retried.
//
// Example:
//
//	task, err := client.Tasks.Retry(ctx, "task_abc123")
//	if err != nil {
//	    fmt.Printf("Failed to retry task: %v\n", err)
//	    return
//	}
//	fmt.Printf("Retry task ID: %s\n", task.ID)
func (s *TasksService) Retry(ctx context.Context, id string) (*Task, error) {
	if id == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "task ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/tasks/%s/retry", id)

	var result Task
	if err := s.client.request(ctx, http.MethodPost, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}
