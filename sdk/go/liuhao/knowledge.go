package liuhao

import (
	"context"
	"fmt"
	"net/http"
)

// KnowledgeService provides methods for managing knowledge bases in the LiuHao AI OS.
//
// Use Client.Knowledge to access this service. Knowledge bases store
// documents that can be semantically searched and used as context for
// agent operations.
type KnowledgeService struct {
	client *Client
}

// ListBases retrieves a paginated list of knowledge bases.
//
// The opts parameter controls pagination and filtering. Pass nil to
// use default options.
//
// Example:
//
//	resp, err := client.Knowledge.ListBases(ctx, &liuhao.ListOptions{
//	    Page:     1,
//	    PageSize: 20,
//	})
func (s *KnowledgeService) ListBases(ctx context.Context, opts *ListOptions) (*KnowledgeBaseListResponse, error) {
	path := "/knowledge/bases"
	query := opts.toQueryParams()
	path = addQueryParams(path, query)

	var result KnowledgeBaseListResponse
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// CreateBase creates a new knowledge base.
//
// The Name field is required. The embedding model can be customized;
// otherwise the system default is used.
//
// Example:
//
//	kb, err := client.Knowledge.CreateBase(ctx, &liuhao.KnowledgeBaseCreateOptions{
//	    Name:           "Product Documentation",
//	    Description:    "Internal product docs and FAQs",
//	    EmbeddingModel: "text-embedding-3-small",
//	})
func (s *KnowledgeService) CreateBase(ctx context.Context, opts *KnowledgeBaseCreateOptions) (*KnowledgeBase, error) {
	if opts == nil {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_options",
				Message:    "knowledge base create options must not be nil",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts.Name == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_name",
				Message:    "knowledge base name is required",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := "/knowledge/bases"

	var result KnowledgeBase
	if err := s.client.request(ctx, http.MethodPost, path, opts, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// GetBase retrieves a single knowledge base by its ID.
//
// Returns a NotFoundError if the knowledge base does not exist.
//
// Example:
//
//	kb, err := client.Knowledge.GetBase(ctx, "kb_abc123")
//	if liuhao.IsNotFoundError(err) {
//	    fmt.Println("Knowledge base not found")
//	}
func (s *KnowledgeService) GetBase(ctx context.Context, id string) (*KnowledgeBase, error) {
	if id == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "knowledge base ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/knowledge/bases/%s", id)

	var result KnowledgeBase
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// DeleteBase deletes a knowledge base and all its contents.
//
// This operation is irreversible. All documents, collections, and
// vectors in the knowledge base will be permanently deleted.
//
// Example:
//
//	err := client.Knowledge.DeleteBase(ctx, "kb_abc123")
func (s *KnowledgeService) DeleteBase(ctx context.Context, id string) error {
	if id == "" {
		return &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_id",
				Message:    "knowledge base ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/knowledge/bases/%s", id)

	if err := s.client.request(ctx, http.MethodDelete, path, nil, nil); err != nil {
		return err
	}
	return nil
}

// UploadDocument uploads a document to a knowledge base.
//
// The document will be automatically chunked and indexed for semantic
// search. The Title field is required. Provide Content for text uploads.
//
// Example:
//
//	doc, err := client.Knowledge.UploadDocument(ctx, "kb_abc123", &liuhao.DocumentUploadOptions{
//	    Title:   "API Reference",
//	    Content: "The API provides endpoints for...",
//	    Type:    "markdown",
//	    Tags:    []string{"api", "reference"},
//	})
func (s *KnowledgeService) UploadDocument(ctx context.Context, baseID string, opts *DocumentUploadOptions) (*Document, error) {
	if baseID == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_base_id",
				Message:    "knowledge base ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts == nil {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_options",
				Message:    "document upload options must not be nil",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts.Title == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_title",
				Message:    "document title is required",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/knowledge/bases/%s/documents", baseID)

	var result Document
	if err := s.client.request(ctx, http.MethodPost, path, opts, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// Search performs a semantic search across knowledge bases.
//
// The Query field is required. You can search across multiple knowledge
// bases by specifying their IDs in KnowledgeBaseIDs, or leave it empty
// to search all accessible knowledge bases.
//
// Example:
//
//	results, err := client.Knowledge.Search(ctx, &liuhao.KnowledgeSearchOptions{
//	    Query:             "How do I authenticate with the API?",
//	    KnowledgeBaseIDs:  []string{"kb_abc123", "kb_def456"},
//	    Limit:             5,
//	})
//	for _, r := range results {
//	    fmt.Printf("[%.2f] %s: %s\n", r.Score, r.DocumentTitle, r.Content)
//	}
func (s *KnowledgeService) Search(ctx context.Context, opts *KnowledgeSearchOptions) ([]SearchResult, error) {
	if opts == nil {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_options",
				Message:    "knowledge search options must not be nil",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts.Query == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_query",
				Message:    "search query is required",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := "/knowledge/search"

	var result struct {
		Results []SearchResult `json:"results"`
	}
	if err := s.client.request(ctx, http.MethodPost, path, opts, &result); err != nil {
		return nil, err
	}
	return result.Results, nil
}

// ListDocuments retrieves a paginated list of documents in a knowledge base.
//
// The opts parameter controls pagination and filtering. Pass nil to use
// default options.
//
// Example:
//
//	resp, err := client.Knowledge.ListDocuments(ctx, "kb_abc123", &liuhao.ListOptions{
//	    Page:     1,
//	    PageSize: 20,
//	})
func (s *KnowledgeService) ListDocuments(ctx context.Context, baseID string, opts *ListOptions) (*DocumentListResponse, error) {
	if baseID == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_base_id",
				Message:    "knowledge base ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/knowledge/bases/%s/documents", baseID)
	query := opts.toQueryParams()
	path = addQueryParams(path, query)

	var result DocumentListResponse
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// DeleteDocument deletes a document from a knowledge base.
//
// All chunks and vectors associated with the document will also be
// removed from the index.
//
// Example:
//
//	err := client.Knowledge.DeleteDocument(ctx, "kb_abc123", "doc_xyz789")
func (s *KnowledgeService) DeleteDocument(ctx context.Context, baseID, docID string) error {
	if baseID == "" {
		return &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_base_id",
				Message:    "knowledge base ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if docID == "" {
		return &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_doc_id",
				Message:    "document ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/knowledge/bases/%s/documents/%s", baseID, docID)

	if err := s.client.request(ctx, http.MethodDelete, path, nil, nil); err != nil {
		return err
	}
	return nil
}

// CreateCollection creates a new collection within a knowledge base.
//
// Collections provide a way to organize documents within a knowledge
// base into logical groups.
//
// Example:
//
//	collection, err := client.Knowledge.CreateCollection(ctx, "kb_abc123", &liuhao.CollectionCreateOptions{
//	    Name:        "Getting Started",
//	    Description: "Onboarding and quick start guides",
//	})
func (s *KnowledgeService) CreateCollection(ctx context.Context, baseID string, opts *CollectionCreateOptions) (*Collection, error) {
	if baseID == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_base_id",
				Message:    "knowledge base ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts == nil {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_options",
				Message:    "collection create options must not be nil",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}
	if opts.Name == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "missing_name",
				Message:    "collection name is required",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/knowledge/bases/%s/collections", baseID)

	var result Collection
	if err := s.client.request(ctx, http.MethodPost, path, opts, &result); err != nil {
		return nil, err
	}
	return &result, nil
}

// ListCollections retrieves a paginated list of collections in a knowledge base.
//
// The opts parameter controls pagination and filtering. Pass nil to use
// default options.
//
// Example:
//
//	resp, err := client.Knowledge.ListCollections(ctx, "kb_abc123", &liuhao.ListOptions{
//	    Page:     1,
//	    PageSize: 20,
//	})
func (s *KnowledgeService) ListCollections(ctx context.Context, baseID string, opts *ListOptions) (*CollectionListResponse, error) {
	if baseID == "" {
		return nil, &ValidationError{
			LiuHaoError: LiuHaoError{
				Code:       "invalid_base_id",
				Message:    "knowledge base ID must not be empty",
				HTTPStatus: http.StatusBadRequest,
			},
		}
	}

	path := fmt.Sprintf("/knowledge/bases/%s/collections", baseID)
	query := opts.toQueryParams()
	path = addQueryParams(path, query)

	var result CollectionListResponse
	if err := s.client.request(ctx, http.MethodGet, path, nil, &result); err != nil {
		return nil, err
	}
	return &result, nil
}
