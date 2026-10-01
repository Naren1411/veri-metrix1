-- Create this private bucket in Supabase Storage.
-- The current application records document metadata and checksums. Before
-- enabling public document uploads, replace local file writes with signed
-- Supabase Storage uploads and keep this bucket private.

insert into storage.buckets (id, name, public)
values ('verimetrix-documents', 'verimetrix-documents', false)
on conflict (id) do update set public=false;
